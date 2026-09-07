"""
camera_manager.py
Gestión de múltiples streams de cámara IP con procesamiento en hilos.

- CameraStream: un hilo por cámara que captura frames (cv2.VideoCapture),
  ejecuta detección + conteo y produce un frame anotado. Mantiene una cola
  de tamaño 2 para minimizar la latencia y se reconecta automáticamente.
- CameraManager: administra el ciclo de vida de todas las cámaras activas,
  genera streams MJPEG y expone estados en tiempo real.

Los eventos de cruce se colocan en una cola global (EVENT_QUEUE) que la
aplicación FastAPI drena para difundirlos por WebSocket.
"""

import os
import time
import queue
import threading
from datetime import datetime
from typing import Dict, Optional, List

import cv2
import numpy as np
import supervision as sv

import config
import database
from detector import get_detector
from counter import LineCounter

# Cola global de eventos de cruce para el WebSocket (thread-safe)
EVENT_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=1000)


def _resolve_source(url: str):
    """Convierte la URL en un argumento válido para cv2.VideoCapture."""
    u = (url or "").strip()
    # Webcam local: "0", "1", ... o "/dev/videoX"
    if u.isdigit():
        return int(u)
    return u


class CameraStream:
    """Hilo de captura + procesamiento para una cámara."""

    def __init__(self, camera: Dict):
        self.id = camera["id"]
        self.name = camera["name"]
        self.url = camera["url"]
        self.source = _resolve_source(camera["url"])

        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Cola con el último frame anotado (maxsize=2 -> baja latencia)
        self._frame_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=2)
        # Último frame crudo (para configurar líneas)
        self._raw_frame: Optional[np.ndarray] = None
        self._raw_lock = threading.Lock()

        self.connected = False
        self.last_error = ""
        self.fps = 0.0
        self.frame_count = 0
        self._reconnect_attempts = 0

        # Componentes de detección/conteo
        self.detector = get_detector()
        self.tracker = self.detector.create_tracker()
        self.line_counter = LineCounter(self.id)

        # ---------------------------------------------------------------
        # Anotadores de Supervision (v0.25+)
        # ---------------------------------------------------------------
        # Paleta de colores por clase (cada clase tiene un color distinto)
        self._palette = sv.ColorPalette.from_hex([
            "#E6194B",  # 0: persona   → rojo
            "#3CB44B",  # 1: bicicleta → verde
            "#4363D8",  # 2: auto      → azul
            "#F58231",  # 3: moto      → naranja
            "#911EB4",  # 4: (reserva)
            "#42D4F4",  # 5: bus       → cian
            "#F032E6",  # 6: (reserva)
            "#BFEF45",  # 7: camión    → lima
        ])

        # Elipse debajo del vehículo (estilo profesional, evita saturar el frame)
        self.ellipse_annotator = sv.EllipseAnnotator(
            color=self._palette,
            color_lookup=sv.ColorLookup.CLASS,
            thickness=2,
        )

        # Etiqueta con clase + tracking_id + confianza
        self.label_annotator = sv.LabelAnnotator(
            color=self._palette,
            color_lookup=sv.ColorLookup.CLASS,
            text_scale=0.45,
            text_thickness=1,
            text_padding=4,
            text_position=sv.Position.TOP_CENTER,
        )

        # Traza / trayectoria de cada vehículo (rastro de movimiento)
        self.trace_annotator = sv.TraceAnnotator(
            color=self._palette,
            color_lookup=sv.ColorLookup.CLASS,
            thickness=2,
            trace_length=30,           # últimos 30 frames de trayectoria
            position=sv.Position.BOTTOM_CENTER,
        )

        # Líneas virtuales con conteos IN/OUT, texto orientado a la línea
        self.line_annotator = sv.LineZoneAnnotator(
            thickness=3,
            color=sv.Color.WHITE,
            text_thickness=1,
            text_scale=0.55,
            text_padding=6,
            custom_in_text="ENTRA",
            custom_out_text="SALE",
            text_orient_to_line=True,
            display_in_count=True,
            display_out_count=True,
            display_text_box=True,
        )

    # ------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"cam-{self.id}")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        self.connected = False

    def reload_lines(self):
        self.line_counter.load_lines()

    def _next_backoff(self) -> float:
        """Backoff exponencial (con techo) para reintentos de conexión."""
        base = max(1, config.settings.reconnect_delay)
        delay = min(base * (2 ** self._reconnect_attempts), 60)
        self._reconnect_attempts += 1
        return delay

    # ------------------------------------------------------------------
    def _open(self) -> bool:
        try:
            cap = cv2.VideoCapture(self.source)
            # Buffer pequeño para reducir latencia en streams RTSP
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if cap.isOpened():
                self._cap = cap
                self.connected = True
                self.last_error = ""
                return True
            cap.release()
        except Exception as exc:
            self.last_error = str(exc)
        self.connected = False
        return False

    def _run(self):
        last_time = time.time()

        while not self._stop.is_set():
            # FPS de procesamiento IA (leído en caliente para reflejar cambios)
            target_interval = 1.0 / max(1, config.settings.target_fps)

            if self._cap is None or not self.connected:
                if not self._open():
                    self.last_error = f"No se pudo conectar a {self.url}"
                    self._publish_placeholder("Conectando...")
                    time.sleep(self._next_backoff())
                    continue

            ok, frame = self._cap.read()
            if not ok or frame is None:
                # Pérdida de conexión -> reconectar
                self.connected = False
                self.last_error = "Se perdió la conexión, reconectando..."
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
                # Si es archivo de video, reiniciar al inicio (no es una
                # falla de conexión real, no aplica backoff)
                if isinstance(self.source, str) and os.path.isfile(self.source):
                    time.sleep(0.5)
                else:
                    time.sleep(self._next_backoff())
                continue

            self._reconnect_attempts = 0

            # Control de FPS objetivo
            now = time.time()
            elapsed = now - last_time
            if elapsed < target_interval:
                time.sleep(max(0, target_interval - elapsed))
            now = time.time()
            self.fps = 1.0 / (now - last_time) if now > last_time else 0.0
            last_time = now

            # Guardar frame crudo (para configuración de líneas)
            with self._raw_lock:
                self._raw_frame = frame.copy()

            # Procesar (detección + conteo + anotación)
            annotated = self._process(frame)
            self.frame_count += 1

            # Publicar frame en la cola (descartar el viejo si está llena)
            if self._frame_q.full():
                try:
                    self._frame_q.get_nowait()
                except queue.Empty:
                    pass
            try:
                self._frame_q.put_nowait(annotated)
            except queue.Full:
                pass

    # ------------------------------------------------------------------
    def _process(self, frame: np.ndarray) -> np.ndarray:
        """
        Ejecuta detección + tracking + conteo y dibuja anotaciones con
        los anotadores modernos de Supervision (v0.25+):

          1. EllipseAnnotator  → elipse de color por clase bajo cada objeto
          2. TraceAnnotator    → trayectoria de movimiento de cada objeto
          3. LabelAnnotator    → etiqueta clase + tracking_id + confianza
          4. LineZoneAnnotator → línea virtual con ENTRA / SALE
        """
        try:
            self.line_counter.reload_if_changed()
            detections = self.detector.detect(frame, self.tracker)

            annotated = frame.copy()

            if len(detections) > 0:
                # --- Construir etiquetas (clase + id + confianza) ----------
                labels = []
                for i in range(len(detections)):
                    class_id = int(detections.class_id[i]) if detections.class_id is not None else -1
                    conf = float(detections.confidence[i]) if detections.confidence is not None else 0.0
                    emoji = config.class_emoji(class_id)
                    cname = config.class_name(class_id)
                    tid = f"#{int(detections.tracker_id[i])} " if detections.tracker_id is not None else ""
                    labels.append(f"{emoji} {tid}{cname} {conf:.0%}")

                # --- Anotaciones visuales (orden importa: trazo → elipse → label)
                # 1. Trayectoria de movimiento
                annotated = self.trace_annotator.annotate(annotated, detections)
                # 2. Elipse por clase (más elegante que bounding box lleno)
                annotated = self.ellipse_annotator.annotate(annotated, detections)
                # 3. Etiqueta
                annotated = self.label_annotator.annotate(annotated, detections, labels)

            # --- Conteo de líneas + emisión de eventos WebSocket ----------
            events = self.line_counter.update(detections)
            for ev in events:
                ev["timestamp"] = datetime.now().isoformat()
                ev["camera_name"] = self.name
                try:
                    EVENT_QUEUE.put_nowait(ev)
                except queue.Full:
                    pass

            # --- Dibujar cada línea virtual con su anotador ---------------
            for wrapper in self.line_counter.lines:
                try:
                    annotated = self.line_annotator.annotate(
                        annotated, line_counter=wrapper.zone
                    )
                except Exception:
                    pass

            # --- Overlay con nombre de cámara y FPS -----------------------
            self._draw_overlay(annotated)
            return annotated

        except Exception as exc:  # pragma: no cover
            print(f"[camera {self.id}] Error procesando frame: {exc}")
            self._draw_overlay(frame)
            return frame

    def _draw_overlay(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        text = f"{self.name} | {self.fps:.1f} FPS"
        cv2.rectangle(frame, (0, 0), (min(w, 360), 26), (0, 0, 0), -1)
        cv2.putText(frame, text, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (80, 220, 120), 1, cv2.LINE_AA)

    def _publish_placeholder(self, message: str):
        """Publica un frame negro con un mensaje (cuando no hay conexión)."""
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(img, self.name, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (0, 140, 255), 2, cv2.LINE_AA)
        cv2.putText(img, message, (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 0, 255), 2, cv2.LINE_AA)
        if self._frame_q.full():
            try:
                self._frame_q.get_nowait()
            except queue.Empty:
                pass
        try:
            self._frame_q.put_nowait(img)
        except queue.Full:
            pass

    # ------------------------------------------------------------------
    def get_frame(self, timeout: float = 5.0) -> Optional[np.ndarray]:
        try:
            return self._frame_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_raw_frame(self) -> Optional[np.ndarray]:
        with self._raw_lock:
            if self._raw_frame is not None:
                return self._raw_frame.copy()
        # Intento directo si el hilo aún no capturó nada
        return None

    def status(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "connected": self.connected,
            "fps": round(self.fps, 1),
            "frames": self.frame_count,
            "last_error": self.last_error,
            "lines": self.line_counter.get_states(),
        }


class CameraManager:
    """Administra todas las cámaras activas."""

    def __init__(self):
        self.streams: Dict[int, CameraStream] = {}
        self._lock = threading.Lock()

    def start_all(self):
        """Inicia todas las cámaras marcadas como activas en la base de datos."""
        for cam in database.get_cameras(only_active=True):
            self.start_camera(cam)

    def start_camera(self, camera: Dict):
        with self._lock:
            if camera["id"] in self.streams:
                return
            stream = CameraStream(camera)
            self.streams[camera["id"]] = stream
        stream.start()

    def stop_camera(self, camera_id: int):
        with self._lock:
            stream = self.streams.pop(camera_id, None)
        if stream:
            stream.stop()

    def restart_camera(self, camera_id: int):
        self.stop_camera(camera_id)
        cam = database.get_camera(camera_id)
        if cam and cam["active"]:
            self.start_camera(cam)

    def get(self, camera_id: int) -> Optional[CameraStream]:
        return self.streams.get(camera_id)

    def reload_lines(self, camera_id: int):
        stream = self.streams.get(camera_id)
        if stream:
            stream.reload_lines()

    def statuses(self) -> List[Dict]:
        return [s.status() for s in self.streams.values()]

    def stop_all(self):
        ids = list(self.streams.keys())
        for cid in ids:
            self.stop_camera(cid)

    def snapshot(self, camera_id: int) -> Optional[np.ndarray]:
        """Obtiene un frame crudo para configurar líneas. Abre la cámara
        temporalmente si no está activa."""
        stream = self.streams.get(camera_id)
        if stream:
            frame = stream.get_raw_frame()
            if frame is not None:
                return frame
        # Cámara no activa -> capturar un único frame directamente
        cam = database.get_camera(camera_id)
        if not cam:
            return None
        source = _resolve_source(cam["url"])
        try:
            cap = cv2.VideoCapture(source)
            ok, frame = cap.read()
            cap.release()
            if ok and frame is not None:
                return frame
        except Exception:
            pass
        return None


# Instancia global
manager = CameraManager()


def _resize_for_stream(frame: np.ndarray) -> np.ndarray:
    """Redimensiona el frame según config.settings.stream_resolution.

    Solo afecta la visualización web; la detección siempre usa la resolución
    original de la cámara.
    """
    target = (config.settings.stream_resolution or "original").lower()
    heights = {"360p": 360, "480p": 480, "720p": 720}
    if target not in heights:
        return frame  # "original" o valor desconocido -> sin cambio
    h, w = frame.shape[:2]
    new_h = heights[target]
    if h <= new_h:
        return frame  # no ampliar si ya es más pequeño
    new_w = int(round(w * (new_h / float(h))))
    return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)


def mjpeg_generator(camera_id: int):
    """Generador de bytes MJPEG para el endpoint de streaming."""
    stream = manager.get(camera_id)
    boundary = b"--frame"
    last_emit = 0.0
    while True:
        # Limitar el FPS de visualización (independiente del FPS de IA)
        display_fps = max(1, config.settings.display_fps)
        min_interval = 1.0 / display_fps
        now = time.time()
        if now - last_emit < min_interval:
            time.sleep(max(0.0, min_interval - (now - last_emit)))
        last_emit = time.time()

        frame = None
        if stream is None:
            stream = manager.get(camera_id)
        if stream is not None:
            frame = stream.get_frame(timeout=5.0)
        if frame is None:
            # Frame de espera
            frame = np.zeros((360, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "Sin senal", (180, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2, cv2.LINE_AA)

        # Redimensionar para la web y aplicar calidad JPEG configurable
        frame = _resize_for_stream(frame)
        quality = int(min(100, max(10, config.settings.stream_quality)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            continue
        yield (boundary + b"\r\n"
               b"Content-Type: image/jpeg\r\n"
               b"Content-Length: " + str(len(buf)).encode() + b"\r\n\r\n"
               + buf.tobytes() + b"\r\n")
