"""
config.py
Configuraciones globales de la aplicación de aforo vehicular.

Los valores por defecto se pueden sobrescribir en tiempo de ejecución
(guardados en la tabla `settings` de SQLite a través de la API /api/status
y /api/settings). Este módulo expone un objeto Settings tipo singleton.
"""

import os

# ---------------------------------------------------------------------------
# Rutas base
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(BASE_DIR, "aforo.db")
MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Clases COCO que nos interesan (id -> nombre en español + emoji)
# ---------------------------------------------------------------------------
# Mapeo de las clases de COCO relevantes para aforo vehicular.
VEHICLE_CLASSES = {
    0: {"name": "Persona", "emoji": "🚶", "color": (255, 178, 29)},
    1: {"name": "Bicicleta", "emoji": "🚲", "color": (207, 210, 49)},
    2: {"name": "Auto", "emoji": "🚗", "color": (72, 173, 255)},
    3: {"name": "Moto", "emoji": "🏍️", "color": (146, 236, 84)},
    5: {"name": "Bus", "emoji": "🚌", "color": (255, 112, 112)},
    7: {"name": "Camión", "emoji": "🚚", "color": (183, 112, 255)},
}

# Lista de ids que se pasan a YOLO para filtrar (class_agnostic=False)
TARGET_CLASS_IDS = list(VEHICLE_CLASSES.keys())


def class_name(class_id: int) -> str:
    """Devuelve el nombre en español de una clase COCO."""
    info = VEHICLE_CLASSES.get(int(class_id))
    return info["name"] if info else f"Clase {class_id}"


def class_emoji(class_id: int) -> str:
    info = VEHICLE_CLASSES.get(int(class_id))
    return info["emoji"] if info else "❓"


# ---------------------------------------------------------------------------
# Etiquetas de dirección/movimiento disponibles para las líneas
# ---------------------------------------------------------------------------
MOVEMENT_LABELS = [
    "NORTE",
    "SUR",
    "ESTE",
    "OESTE",
    "GIRO_IZQ",
    "GIRO_DER",
    "RECTO",
    "ENTRADA",
    "SALIDA",
]


# ---------------------------------------------------------------------------
# Configuración global (valores por defecto)
# ---------------------------------------------------------------------------
class Settings:
    """Configuración global mutable en tiempo de ejecución."""

    def __init__(self):
        # Umbral de confianza para las detecciones
        self.confidence: float = 0.5
        # Modelo YOLOv8 a usar: n / s / m / l / x
        self.model_size: str = "m"
        # FPS objetivo de procesamiento por cámara
        self.target_fps: int = 15
        # Dispositivo: "auto" (detecta GPU), "cpu" o "cuda" (GPU)
        self.device: str = "auto"
        # IOU threshold para NMS
        self.iou: float = 0.5

        # --- Stream de video (independiente del procesamiento IA) ---
        # FPS de visualización del stream web (no afecta la detección)
        self.display_fps: int = 25
        # Calidad JPEG del stream MJPEG (0-100)
        self.stream_quality: int = 80
        # Resolución máxima del stream web: "original", "720p", "480p", "360p"
        self.stream_resolution: str = "original"
        # Tiempo de reconexión de cámara en segundos
        self.reconnect_delay: int = 5

        # --- Base de datos ---
        # Retención de datos en SQLite (días, 0 = infinito)
        self.data_retention_days: int = 90

        # --- Clases activas para detección (IDs COCO) ---
        # 0=persona, 1=bicicleta, 2=auto, 3=moto, 5=bus, 7=camión
        self.active_classes: list = [0, 1, 2, 3, 5, 7]

    @property
    def model_name(self) -> str:
        return f"yolov8{self.model_size}.pt"

    @property
    def resolved_device(self) -> str:
        """
        Resuelve el dispositivo real a usar.

        - "auto"  -> "cuda" si hay GPU NVIDIA disponible, si no "cpu".
        - "cuda"/"gpu" -> "cuda" si está disponible, si no "cpu" (con aviso).
        - "cpu"   -> "cpu".
        """
        pref = (self.device or "auto").lower()
        try:
            import torch
            has_cuda = torch.cuda.is_available()
        except Exception:
            has_cuda = False

        if pref in ("auto", ""):
            return "cuda:0" if has_cuda else "cpu"
        if pref in ("cuda", "gpu", "cuda:0", "0"):
            if has_cuda:
                return "cuda:0"
            print("[config] Se solicitó GPU pero CUDA no está disponible; usando CPU.")
            return "cpu"
        return "cpu"

    @property
    def model_path(self) -> str:
        return os.path.join(MODELS_DIR, self.model_name)

    @property
    def active_class_ids(self) -> list:
        """IDs de clases activas que además existen en VEHICLE_CLASSES."""
        valid = set(VEHICLE_CLASSES.keys())
        ids = [int(c) for c in (self.active_classes or []) if int(c) in valid]
        return ids or list(valid)

    def to_dict(self) -> dict:
        return {
            "confidence": self.confidence,
            "model_size": self.model_size,
            "target_fps": self.target_fps,
            "device": self.device,
            "iou": self.iou,
            "display_fps": self.display_fps,
            "stream_quality": self.stream_quality,
            "stream_resolution": self.stream_resolution,
            "reconnect_delay": self.reconnect_delay,
            "data_retention_days": self.data_retention_days,
            "active_classes": list(self.active_classes),
        }

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if not hasattr(self, key) or value is None:
                continue
            if key == "active_classes":
                # Normalizar a lista de enteros
                try:
                    value = [int(v) for v in value]
                except Exception:
                    continue
            setattr(self, key, value)


# Instancia singleton global
settings = Settings()

# Servidor
HOST = "0.0.0.0"
PORT = 8000
