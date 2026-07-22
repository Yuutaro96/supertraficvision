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
                self.device = config.settings.device
                print(f"[detector] Modelo {model_name} cargado en {self.device}.")
            except Exception as exc:  # pragma: no cover
                print(f"[detector] ERROR cargando el modelo: {exc}")
                self.model = None

    def ensure_model(self):
        """Recarga el modelo si cambió el tamaño o el dispositivo en settings."""
        if (self.model is None
                or self.model_size != config.settings.model_size
                or self.device != config.settings.device):
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

        try:
            with self._model_lock:
                results = self.model(
                    frame,
                    conf=config.settings.confidence,
                    iou=config.settings.iou,
                    classes=config.TARGET_CLASS_IDS,
                    device=config.settings.device,
                    verbose=False,
                )[0]
            detections = sv.Detections.from_ultralytics(results)
        except Exception as exc:  # pragma: no cover
            print(f"[detector] Error en detección: {exc}")
            return sv.Detections.empty()

        # Tracking para evitar doble conteo
        if tracker is not None and len(detections) > 0:
            detections = tracker.update_with_detections(detections)

        return detections

    @staticmethod
    def create_tracker() -> sv.ByteTrack:
        """Crea un tracker ByteTrack independiente por cámara."""
        return sv.ByteTrack()


# Acceso conveniente
def get_detector() -> VehicleDetector:
    return VehicleDetector()
