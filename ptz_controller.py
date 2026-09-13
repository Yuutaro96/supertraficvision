"""
ptz_controller.py
Capa de abstracción para controlar cámaras PTZ (pan/tilt/zoom) de distintas
marcas/protocolos desde una interfaz común, para que el resto de la app
(endpoints de main.py, interfaz web) no dependa de los detalles de cada
fabricante.

Cada cámara PTZ guarda en la base de datos:
  - control_protocol: qué controlador usar ("ezviz_cloud", "onvif", "none")
  - control_config:   JSON con los datos que ese protocolo necesita
                      (ej. {"device_serial": "K30105213"} para EZVIZ)

Para agregar una marca/protocolo nuevo en el futuro: solo se escribe una
clase más con la misma interfaz y se registra en `get_ptz_controller`.
"""

import json
import threading
from typing import Optional

import config

DIRECTIONS = ("up", "down", "left", "right")


class PTZError(Exception):
    """Error de control PTZ (credenciales, comando no soportado, fallo de red...)."""


class PTZController:
    """Interfaz común que debe implementar cada protocolo."""

    def move(self, direction: str, speed: int = 5) -> None:
        raise NotImplementedError

    def stop(self, direction: str = "up") -> None:
        raise NotImplementedError

    def zoom(self, direction: str) -> None:
        """direction: 'in' o 'out'. No todos los modelos tienen zoom motorizado
        real (muchas cámaras PTZ económicas solo hacen zoom digital en la app,
        sin motor); si el comando no es soportado, debe lanzar PTZError."""
        raise NotImplementedError

    def save_preset(self, name: str) -> None:
        raise NotImplementedError

    def goto_preset(self, name: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# EZVIZ (nube) — usa la librería pyezvizapi, la misma que implementa la
# integración oficial de EZVIZ en Home Assistant. Requiere la cuenta EZVIZ
# (no las credenciales de admin locales de la cámara) configurada en las
# variables de entorno EZVIZ_ACCOUNT / EZVIZ_PASSWORD.
# ---------------------------------------------------------------------------
_ezviz_client = None
_ezviz_client_lock = threading.Lock()


def _get_ezviz_client():
    """Cliente EZVIZ compartido (una sola sesión de cuenta para todas las
    cámaras EZVIZ), con login perezoso la primera vez que se necesita."""
    global _ezviz_client
    with _ezviz_client_lock:
        if _ezviz_client is None:
            if not config.EZVIZ_ACCOUNT or not config.EZVIZ_PASSWORD:
                raise PTZError(
                    "EZVIZ_ACCOUNT/EZVIZ_PASSWORD no configurados (variables de entorno)."
                )
            from pyezvizapi import EzvizClient, PyEzvizError

            client = EzvizClient(config.EZVIZ_ACCOUNT, config.EZVIZ_PASSWORD)
            try:
                client.login()
            except PyEzvizError as exc:
                raise PTZError(f"No se pudo iniciar sesión en EZVIZ: {exc}") from exc
            _ezviz_client = client
        return _ezviz_client


class EzvizCloudPTZController(PTZController):
    """Control PTZ vía la nube de EZVIZ (open.ys7.com / apiieu.ezvizlife.com).

    Necesario para cámaras "Smart Home" (como la CS-C8PF) que bloquean
    ONVIF/ISAPI local y solo exponen el PTZ a través de la app/nube.
    """

    def __init__(self, device_serial: str):
        if not device_serial:
            raise PTZError("Falta device_serial en control_config de la cámara.")
        self.device_serial = device_serial

    def move(self, direction: str, speed: int = 5) -> None:
        if direction not in DIRECTIONS:
            raise PTZError(f"Dirección inválida: {direction!r} (usar {DIRECTIONS})")
        speed = max(1, min(10, int(speed)))
        client = _get_ezviz_client()
        from pyezvizapi import PyEzvizError
        try:
            client.ptz_control(direction.upper(), self.device_serial, "START", speed)
        except PyEzvizError as exc:
            raise PTZError(f"Error moviendo la cámara: {exc}") from exc

    def stop(self, direction: str = "up") -> None:
        if direction not in DIRECTIONS:
            direction = "up"  # el comando STOP solo detiene el motor, la dirección es indiferente
        client = _get_ezviz_client()
        from pyezvizapi import PyEzvizError
        try:
            client.ptz_control(direction.upper(), self.device_serial, "STOP", 5)
        except PyEzvizError as exc:
            raise PTZError(f"Error deteniendo la cámara: {exc}") from exc

    def zoom(self, direction: str) -> None:
        # NOTA: pyezvizapi no expone un método de zoom documentado/probado
        # (su API pública solo cubre up/down/left/right). Este comando es
        # un intento best-effort reutilizando el mismo canal genérico de
        # ptz_control; puede no funcionar si el modelo no tiene zoom
        # motorizado (muchas PTZ "Smart Home" económicas, como la CS-C8PF,
        # solo hacen zoom digital dentro de la app, sin motor real).
        cmd = "ZOOM_IN" if direction == "in" else "ZOOM_OUT" if direction == "out" else None
        if cmd is None:
            raise PTZError("direction debe ser 'in' o 'out'")
        client = _get_ezviz_client()
        from pyezvizapi import PyEzvizError
        try:
            client.ptz_control(cmd, self.device_serial, "START", 5)
            client.ptz_control(cmd, self.device_serial, "STOP", 5)
        except PyEzvizError as exc:
            raise PTZError(
                f"Zoom no soportado o falló en esta cámara (comando no verificado oficialmente): {exc}"
            ) from exc

    def save_preset(self, name: str) -> None:
        raise PTZError(
            "Esta cámara no expone una API de posiciones guardadas (presets) — "
            "usa la app EZVIZ para reposicionarla manualmente cuando se desalinee."
        )

    def goto_preset(self, name: str) -> None:
        raise PTZError(
            "Esta cámara no expone una API de posiciones guardadas (presets) — "
            "usa la app EZVIZ para reposicionarla manualmente cuando se desalinee."
        )


# ---------------------------------------------------------------------------
# ONVIF (local) — placeholder para cámaras estándar que sí expongan el
# servicio ONVIF (a diferencia de esta EZVIZ). Se implementa cuando llegue
# la primera cámara del corredor que lo soporte.
# ---------------------------------------------------------------------------
class OnvifPTZController(PTZController):
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host, self.port, self.username, self.password = host, port, username, password

    def _not_implemented(self):
        raise PTZError("Control ONVIF todavía no implementado en supertraficvision.")

    def move(self, direction: str, speed: int = 5) -> None:
        self._not_implemented()

    def stop(self, direction: str = "up") -> None:
        self._not_implemented()

    def zoom(self, direction: str) -> None:
        self._not_implemented()

    def save_preset(self, name: str) -> None:
        self._not_implemented()

    def goto_preset(self, name: str) -> None:
        self._not_implemented()


# ---------------------------------------------------------------------------
def get_ptz_controller(camera: dict) -> PTZController:
    """Fábrica: a partir de una fila de la tabla `cameras`, devuelve el
    controlador PTZ correcto según su control_protocol."""
    protocol = camera.get("control_protocol") or "none"
    raw_config = camera.get("control_config")
    try:
        cfg = json.loads(raw_config) if raw_config else {}
    except (TypeError, ValueError):
        cfg = {}

    if protocol == "ezviz_cloud":
        return EzvizCloudPTZController(device_serial=cfg.get("device_serial", ""))
    if protocol == "onvif":
        return OnvifPTZController(
            host=cfg.get("host", ""), port=int(cfg.get("port", 80)),
            username=cfg.get("username", ""), password=cfg.get("password", ""),
        )
    raise PTZError(f"Esta cámara no tiene control PTZ configurado (protocolo: {protocol!r}).")
