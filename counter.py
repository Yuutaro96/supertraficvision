"""
counter.py
Contadores de líneas virtuales usando supervision.LineZone.

Cada cámara tiene un LineCounter que gestiona todas sus líneas. Al procesar
un frame con las detecciones (ya trackeadas), se evalúa el cruce de cada
objeto por cada línea y se registra el cruce (IN/OUT) en la base de datos.
"""

import time
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

import numpy as np
import supervision as sv

import config
import database

# Cuántos tracker_id recientes se conservan en el historial de votos de
# clase por cámara, para no crecer sin límite en una sesión de días.
MAX_TRACKED_VOTES = 2000


class LineZoneWrapper:
    """
    Envuelve un sv.LineZone junto con metadatos de la línea.

    Mejoras con Supervision v0.25+:
    - triggering_anchors: usa BOTTOM_CENTER (punto inferior del bbox) para
      detectar el cruce, que es más preciso para vehículos (el punto de
      contacto con el suelo, no el centro de la caja).
    - minimum_crossing_threshold: requiere que el objeto aparezca al menos
      2 frames del otro lado de la línea antes de contar, evitando falsos
      positivos por detecciones inestables.
    """

    def __init__(self, line_row: Dict):
        self.id = line_row["id"]
        self.name = line_row["name"]
        self.movement = line_row["movement"]
        # AMBOS (por defecto) | IN | OUT: qué sentido de cruce se persiste.
        # El otro sentido se sigue detectando (in_count/out_count del zone
        # siguen incrementando) pero no se guarda en la base ni se emite.
        self.count_side = line_row.get("count_side") or "AMBOS"
        self.x1, self.y1 = line_row["x1"], line_row["y1"]
        self.x2, self.y2 = line_row["x2"], line_row["y2"]
        start = sv.Point(self.x1, self.y1)
        end = sv.Point(self.x2, self.y2)
        self.zone = sv.LineZone(
            start=start,
            end=end,
            # BOTTOM_CENTER → punto inferior del bbox (contacto con el suelo)
            triggering_anchors=[sv.Position.BOTTOM_CENTER],
            # 2 frames consecutivos del otro lado → conteo más estable
            minimum_crossing_threshold=2,
        )

    def as_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "movement": self.movement,
            "count_side": self.count_side,
            "in_count": self.zone.in_count,
            "out_count": self.zone.out_count,
        }


class LineCounter:
    """Gestiona todas las líneas virtuales de una cámara."""

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.lines: List[LineZoneWrapper] = []
        # Historial de clases detectadas por tracker_id, para votar la clase
        # más frecuente al momento del cruce en vez de confiar en un único
        # frame (donde YOLO puede confundir, por ejemplo, auto con camión).
        self._track_votes: Dict[int, Counter] = defaultdict(Counter)
        # Pares de líneas (entrada+salida) para confirmar giros por tracker_id.
        self.pairs: List[Dict] = []
        self._entry_pairs_by_line: Dict[int, List[Dict]] = defaultdict(list)
        self._exit_pairs_by_line: Dict[int, List[Dict]] = defaultdict(list)
        # tracker_id -> [{"pair_id", "expires_at" (monotonic), "class_id"}]
        self._pending_pairs: Dict[int, List[Dict]] = defaultdict(list)
        self._pair_confirmed: Dict[int, int] = defaultdict(int)
        self._pair_expired: Dict[int, int] = defaultdict(int)
        self.load_lines()
        self.load_pairs()

    def load_lines(self):
        """(Re)carga las líneas de la cámara desde la base de datos.

        Conserva el wrapper (y por lo tanto los contadores IN/OUT en memoria)
        de las líneas que no cambiaron, para que editar o agregar una línea
        no resetee el conteo en vivo de las demás líneas de la cámara.
        """
        rows = database.get_lines(self.camera_id)
        existing = {w.id: w for w in self.lines}
        new_lines = []
        for r in rows:
            w = existing.get(r["id"])
            unchanged = w is not None and (
                w.name, w.movement, w.x1, w.y1, w.x2, w.y2
            ) == (r["name"], r["movement"], r["x1"], r["y1"], r["x2"], r["y2"])
            if unchanged:
                # count_side no afecta la geometría del zone: se refresca en
                # el wrapper existente sin resetear sus contadores en vivo.
                w.count_side = r.get("count_side") or "AMBOS"
                new_lines.append(w)
            else:
                new_lines.append(LineZoneWrapper(r))
        self.lines = new_lines

    def reload_if_changed(self):
        """Recarga si el número de líneas en DB difiere del actual."""
        rows = database.get_lines(self.camera_id)
        current_ids = {l.id for l in self.lines}
        db_ids = {r["id"] for r in rows}
        if current_ids != db_ids:
            self.load_lines()
            self.load_pairs()

    def load_pairs(self):
        """(Re)carga los pares entrada/salida (giros confirmados) de la cámara.

        Se apoya en self.lines (ya cargadas) para resolver el count_side de
        las líneas de entrada/salida de cada par, así no hace falta otra
        consulta a la base por línea.
        """
        line_side_by_id = {w.id: w.count_side for w in self.lines}
        rows = database.get_line_pairs(self.camera_id)
        self.pairs = []
        self._entry_pairs_by_line = defaultdict(list)
        self._exit_pairs_by_line = defaultdict(list)
        for r in rows:
            pair = {
                "id": r["id"],
                "name": r["name"],
                "movement": r["movement"],
                "entry_line_id": r["entry_line_id"],
                "exit_line_id": r["exit_line_id"],
                "virtual_line_id": r["virtual_line_id"],
                "max_seconds": r["max_seconds"],
                # Si la línea de entrada/salida ya no existe (borrada), AMBOS
                # como fallback nunca dispara (no hay línea que la cruce).
                "entry_side": line_side_by_id.get(r["entry_line_id"], "AMBOS"),
                "exit_side": line_side_by_id.get(r["exit_line_id"], "AMBOS"),
            }
            self.pairs.append(pair)
            self._entry_pairs_by_line[pair["entry_line_id"]].append(pair)
            self._exit_pairs_by_line[pair["exit_line_id"]].append(pair)

    def _update_votes(self, detections: sv.Detections) -> None:
        if detections.tracker_id is None or detections.class_id is None:
            return
        for tid, cid in zip(detections.tracker_id, detections.class_id):
            self._track_votes[int(tid)][int(cid)] += 1
        # Podar el historial si crece demasiado (sesiones de varios días).
        if len(self._track_votes) > MAX_TRACKED_VOTES:
            cutoff = max(self._track_votes) - MAX_TRACKED_VOTES
            for tid in [t for t in self._track_votes if t < cutoff]:
                del self._track_votes[tid]

    def _voted_class(self, tracker_id: int, fallback_class_id: int) -> int:
        """Clase más frecuente detectada para este track (moda), o la del
        frame actual si el track no tiene historial."""
        votes = self._track_votes.get(int(tracker_id))
        if not votes:
            return fallback_class_id
        return votes.most_common(1)[0][0]

    def _prune_expired_pairs(self, now: float) -> None:
        for tid in list(self._pending_pairs):
            kept = []
            for entry in self._pending_pairs[tid]:
                if entry["expires_at"] < now:
                    self._pair_expired[entry["pair_id"]] += 1
                else:
                    kept.append(entry)
            if kept:
                self._pending_pairs[tid] = kept
            else:
                del self._pending_pairs[tid]

    def _register_pair_entries(self, wrapper_id: int, tracker_id: int,
                                direction: str, class_id: int, now: float) -> None:
        for pair in self._entry_pairs_by_line.get(wrapper_id, []):
            if pair["entry_side"] not in ("AMBOS", direction):
                continue
            self._pending_pairs[tracker_id].append({
                "pair_id": pair["id"],
                "expires_at": now + pair["max_seconds"],
                "class_id": class_id,
            })

    def _try_confirm_pairs(self, wrapper_id: int, tracker_id: int,
                            direction: str, fallback_class_id: int, now: float) -> List[Dict]:
        """Si esta línea es la salida de algún par y hay una entrada pendiente
        para este tracker_id dentro de la ventana, confirma el giro: registra
        UN conteo bajo la línea virtual del par y limpia todos los pendientes
        de este vehículo (solo puede completar un movimiento a la vez)."""
        events: List[Dict] = []
        pending = self._pending_pairs.get(tracker_id)
        if not pending:
            return events
        exit_pairs = {p["id"]: p for p in self._exit_pairs_by_line.get(wrapper_id, [])}
        if not exit_pairs:
            return events
        match = None
        for entry in pending:
            pair = exit_pairs.get(entry["pair_id"])
            if pair and pair["exit_side"] in ("AMBOS", direction) and entry["expires_at"] >= now:
                match = pair
                break
        if match is None:
            return events

        class_id = self._voted_class(tracker_id, fallback_class_id)
        cname = config.class_name(class_id)
        database.record_count(
            camera_id=self.camera_id,
            line_id=match["virtual_line_id"],
            class_name=cname,
            direction="IN",
            count=1,
        )
        events.append({
            "camera_id": self.camera_id,
            "line_id": match["virtual_line_id"],
            "line_name": match["name"],
            "movement": match["movement"],
            "class_name": cname,
            "emoji": config.class_emoji(class_id),
            "direction": "IN",
        })
        self._pair_confirmed[match["id"]] += 1
        # Un vehículo solo completa un giro: se descartan sus demás pendientes.
        del self._pending_pairs[tracker_id]
        return events

    def get_pair_states(self) -> List[Dict]:
        return [
            {
                "id": p["id"],
                "name": p["name"],
                "movement": p["movement"],
                "confirmed": self._pair_confirmed.get(p["id"], 0),
                "expired": self._pair_expired.get(p["id"], 0),
            }
            for p in self.pairs
        ]

    def update(self, detections: sv.Detections) -> List[Dict]:
        """
        Evalúa el cruce de las detecciones sobre cada línea.
        Registra los cruces en la base de datos y devuelve la lista de
        eventos generados en este frame (para emitir por WebSocket).
        """
        events: List[Dict] = []
        if detections is None or len(detections) == 0:
            return events

        self._update_votes(detections)

        now = time.monotonic()
        if self.pairs:
            self._prune_expired_pairs(now)

        for wrapper in self.lines:
            try:
                crossed_in, crossed_out = wrapper.zone.trigger(detections)
            except Exception as exc:  # pragma: no cover
                print(f"[counter] Error en trigger de línea {wrapper.id}: {exc}")
                continue

            for mask, direction in ((crossed_in, "IN"), (crossed_out, "OUT")):
                if mask is None:
                    continue
                idxs = np.where(mask)[0]
                for i in idxs:
                    class_id = int(detections.class_id[i]) if detections.class_id is not None else -1
                    tracker_id = int(detections.tracker_id[i]) if detections.tracker_id is not None else None
                    if tracker_id is not None:
                        class_id = self._voted_class(tracker_id, class_id)

                        # Par de líneas: si esta línea es entrada/salida de algún
                        # giro, llevar la cuenta independientemente de si su
                        # propio count_side persiste este cruce como línea suelta.
                        if wrapper.id in self._entry_pairs_by_line:
                            self._register_pair_entries(wrapper.id, tracker_id, direction, class_id, now)
                        if wrapper.id in self._exit_pairs_by_line:
                            events.extend(self._try_confirm_pairs(
                                wrapper.id, tracker_id, direction, class_id, now))

                    # Registrar el cruce de la línea "suelta", filtrando por su
                    # sentido configurado (independiente del conteo por pares).
                    if wrapper.count_side != "AMBOS" and wrapper.count_side != direction:
                        continue
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
