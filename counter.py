"""
counter.py
Contadores de líneas virtuales usando supervision.LineZone.

Cada cámara tiene un LineCounter que gestiona todas sus líneas. Al procesar
un frame con las detecciones (ya trackeadas), se evalúa el cruce de cada
objeto por cada línea y se registra el cruce (IN/OUT) en la base de datos.
"""

from typing import Dict, List, Tuple

import numpy as np
import supervision as sv

import config
import database


class LineZoneWrapper:
    """Envuelve un sv.LineZone junto con metadatos de la línea."""

    def __init__(self, line_row: Dict):
        self.id = line_row["id"]
        self.name = line_row["name"]
        self.movement = line_row["movement"]
        self.x1, self.y1 = line_row["x1"], line_row["y1"]
        self.x2, self.y2 = line_row["x2"], line_row["y2"]
        start = sv.Point(self.x1, self.y1)
        end = sv.Point(self.x2, self.y2)
        self.zone = sv.LineZone(start=start, end=end)

    def as_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "movement": self.movement,
            "in_count": self.zone.in_count,
            "out_count": self.zone.out_count,
        }


class LineCounter:
    """Gestiona todas las líneas virtuales de una cámara."""

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.lines: List[LineZoneWrapper] = []
        self.load_lines()

    def load_lines(self):
        """(Re)carga las líneas de la cámara desde la base de datos."""
        rows = database.get_lines(self.camera_id)
        self.lines = [LineZoneWrapper(r) for r in rows]

    def reload_if_changed(self):
        """Recarga si el número de líneas en DB difiere del actual."""
        rows = database.get_lines(self.camera_id)
        current_ids = {l.id for l in self.lines}
        db_ids = {r["id"] for r in rows}
        if current_ids != db_ids:
            # Se agregaron/eliminaron líneas -> recargar (reinicia contadores)
            self.lines = [LineZoneWrapper(r) for r in rows]

    def update(self, detections: sv.Detections) -> List[Dict]:
        """
        Evalúa el cruce de las detecciones sobre cada línea.
        Registra los cruces en la base de datos y devuelve la lista de
        eventos generados en este frame (para emitir por WebSocket).
        """
        events: List[Dict] = []
        if detections is None or len(detections) == 0:
            return events

        for wrapper in self.lines:
            try:
                crossed_in, crossed_out = wrapper.zone.trigger(detections)
            except Exception as exc:  # pragma: no cover
                print(f"[counter] Error en trigger de línea {wrapper.id}: {exc}")
                continue

            # Registrar cruces IN
            for mask, direction in ((crossed_in, "IN"), (crossed_out, "OUT")):
                if mask is None:
                    continue
                idxs = np.where(mask)[0]
                for i in idxs:
                    class_id = int(detections.class_id[i]) if detections.class_id is not None else -1
                    cname = config.class_name(class_id)
                    database.record_count(
                        camera_id=self.camera_id,
                        line_id=wrapper.id,
                        class_name=cname,
                        direction=direction,
                        count=1,
                    )
                    events.append({
                        "camera_id": self.camera_id,
                        "line_id": wrapper.id,
                        "line_name": wrapper.name,
                        "movement": wrapper.movement,
                        "class_name": cname,
                        "emoji": config.class_emoji(class_id),
                        "direction": direction,
                    })
        return events

    def get_line_zones(self) -> List[sv.LineZone]:
        return [w.zone for w in self.lines]

    def get_states(self) -> List[Dict]:
        return [w.as_dict() for w in self.lines]
