"""
detector.py
Detección de vehículos/personas con YOLOv8m + tracking con Supervision ByteTrack.

La clase VehicleDetector carga el modelo una sola vez (singleton) y expone
un método `detect` que devuelve un objeto `sv.Detections` ya filtrado a las
clases de interés y con tracking_id asignado por ByteTrack.
"""

import os
import threading
from typing import Optional

import numpy as np
import supervision as sv

import config


def _patch_torch_load_for_ultralytics():
    """
    Compatibilidad con PyTorch >= 2.6.

    Desde PyTorch 2.6 el parámetro `weights_only` de `torch.load` pasó a ser
    `True` por defecto, lo que impide cargar los pesos oficiales de YOLOv8
    (lanzando "Weights only load failed ... Unsupported global").

    Los pesos oficiales de Ultralytics provienen de una fuente de confianza,
    por lo que aquí:
      1) Registramos las clases de Ultralytics como "safe globals".
      2) Forzamos weights_only=False como respaldo para la carga del modelo.
    """
    try:
        import torch  # import diferido

        # (1) Intentar registrar las clases de Ultralytics como seguras.
        try:
            from torch.serialization import add_safe_globals
            safe = []
            try:
                from ultralytics.nn.tasks import DetectionModel
                safe.append(DetectionModel)
            except Exception:
                pass
            try:
                import torch.nn as nn
                safe.extend([nn.Sequential, nn.ModuleList, nn.Conv2d])
            except Exception:
                pass
            if safe:
                add_safe_globals(safe)
        except Exception:
            pass

        # (2) Respaldo: envolver torch.load para forzar weights_only=False.
        if not getattr(torch, "_aforo_load_patched", False):
            _orig_load = torch.load

            def _patched_load(*args, **kwargs):
                kwargs.setdefault("weights_only", False)
                return _orig_load(*args, **kwargs)

            torch.load = _patched_load
            torch._aforo_load_patched = True
    except Exception as exc:  # pragma: no cover
        print(f"[detector] Aviso: no se pudo aplicar el parche de torch.load: {exc}")


class VehicleDetector:
    """Detector singleton basado en YOLOv8 + ByteTrack."""

    _instance: Optional["VehicleDetector"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # Singleton thread-safe
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.model = None
        self.model_size = None
        self.device = None
        self._model_lock = threading.Lock()
        self._load_model()

    # ------------------------------------------------------------------
    # Carga del modelo
    # ------------------------------------------------------------------
    def _load_model(self):
        """Carga (o recarga) YOLOv8 según config.settings, descargando si falta."""
        _patch_torch_load_for_ultralytics()  # compatibilidad PyTorch >= 2.6
        from ultralytics import YOLO  # import diferido para acelerar arranque

        model_path = config.settings.model_path
        model_name = config.settings.model_name

        # Si el peso no está en models/, YOLO lo descarga automáticamente al
        # instanciarse con el nombre. Lo movemos luego a models/ para reuso.
        with self._model_lock:
            try:
                if os.path.exists(model_path):
                    self.model = YOLO(model_path)
                else:
                    # YOLO descarga el peso oficial al directorio actual
                    self.model = YOLO(model_name)
                    # Intentar reubicar el archivo descargado a models/
                    if os.path.exists(model_name):
                        try:
                            os.replace(model_name, model_path)
                            self.model = YOLO(model_path)
                        except OSError:
                            pass
                self.model_size = config.settings.model_size
                self.device = config.settings.resolved_device
                # Mover el modelo al dispositivo resuelto (GPU si está disponible)
                try:
                    self.model.to(self.device)
                except Exception:
                    pass
                dev_label = "GPU (CUDA)" if str(self.device).startswith("cuda") else "CPU"
                print(f"[detector] Modelo {model_name} cargado en {self.device} [{dev_label}].")
            except Exception as exc:  # pragma: no cover
                print(f"[detector] ERROR cargando el modelo: {exc}")
                self.model = None

    def ensure_model(self):
        """Recarga el modelo si cambió el tamaño o el dispositivo en settings."""
        if (self.model is None
                or self.model_size != config.settings.model_size
                or self.device != config.settings.resolved_device):
            self._load_model()

    # ------------------------------------------------------------------
    # Detección
    # ------------------------------------------------------------------
    def detect(self, frame: np.ndarray, tracker: Optional[sv.ByteTrack] = None) -> sv.Detections:
        """
        Ejecuta detección sobre un frame BGR y devuelve sv.Detections
        filtrado a las clases de interés y con tracking_id (si se pasa tracker).
        """
        if self.model is None:
            self._load_model()
        if self.model is None:
            return sv.Detections.empty()

        class_confidence = config.settings.class_confidence
        # Umbral usado en la inferencia: el más bajo entre el global y los
        # overrides por clase, para no descartar antes de tiempo detecciones
        # de una clase con umbral más permisivo (se filtran después, por clase).
        base_conf = min([config.settings.confidence, *class_confidence.values()]) \
            if class_confidence else config.settings.confidence

        try:
            with self._model_lock:
                results = self.model(
                    frame,
                    conf=base_conf,
                    iou=config.settings.iou,
                    classes=config.settings.active_class_ids,
                    device=config.settings.resolved_device,
                    imgsz=config.settings.imgsz,
                    half=str(self.device).startswith("cuda"),
                    verbose=False,
                )[0]
            detections = sv.Detections.from_ultralytics(results)
        except Exception as exc:  # pragma: no cover
            print(f"[detector] Error en detección: {exc}")
            return sv.Detections.empty()

        detections = _filter_by_class_confidence(detections, class_confidence, config.settings.confidence)

        # Tracking para evitar doble conteo
        if tracker is not None and len(detections) > 0:
            detections = tracker.update_with_detections(detections)

        return detections

    @staticmethod
    def create_tracker() -> sv.ByteTrack:
        """
        Crea un tracker ByteTrack independiente por cámara, con los
        parámetros configurables en Ajustes (activación, buffer de tracks
        perdidos, frames consecutivos mínimos).

        Nota: sv.ByteTrack está marcado como deprecated desde v0.28 pero
        sigue funcional. Se suprime el warning para no saturar los logs.
        """
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            return sv.ByteTrack(
                track_activation_threshold=config.settings.track_activation_threshold,
                lost_track_buffer=config.settings.lost_track_buffer,
                minimum_consecutive_frames=config.settings.minimum_consecutive_frames,
            )


def _filter_by_class_confidence(detections: sv.Detections, class_confidence: dict,
                                default_confidence: float) -> sv.Detections:
    """Aplica umbrales de confianza distintos por clase, post-inferencia.

    La inferencia ya corrió con el umbral más bajo necesario; aquí se
    descartan las detecciones que no alcancen el umbral de SU clase.
    """
    if not class_confidence or len(detections) == 0 or detections.confidence is None:
        return detections
    thresholds = np.array([
        class_confidence.get(int(cid), default_confidence) for cid in detections.class_id
    ])
    keep = detections.confidence >= thresholds
    return detections[keep]


# Acceso conveniente
def get_detector() -> VehicleDetector:
    return VehicleDetector()
