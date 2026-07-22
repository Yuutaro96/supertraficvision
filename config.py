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
        # Dispositivo: "cpu" o "cuda" (GPU)
        self.device: str = "cpu"
        # IOU threshold para NMS
        self.iou: float = 0.5

    @property
    def model_name(self) -> str:
        return f"yolov8{self.model_size}.pt"

    @property
    def model_path(self) -> str:
        return os.path.join(MODELS_DIR, self.model_name)

    def to_dict(self) -> dict:
        return {
            "confidence": self.confidence,
            "model_size": self.model_size,
            "target_fps": self.target_fps,
            "device": self.device,
            "iou": self.iou,
        }

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)


# Instancia singleton global
settings = Settings()

# Servidor
HOST = "0.0.0.0"
PORT = 8000
