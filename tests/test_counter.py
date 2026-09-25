import time

import numpy as np
import supervision as sv

import database
from counter import LineCounter


def _detections(tracker_ids, class_ids, xyxy=None):
    n = len(tracker_ids)
    return sv.Detections(
        xyxy=xyxy if xyxy is not None else np.zeros((n, 4), dtype=np.float32),
        tracker_id=np.array(tracker_ids, dtype=int),
        class_id=np.array(class_ids, dtype=int),
        confidence=np.ones(n, dtype=np.float32),
    )


def test_crossing_uses_majority_class_across_frames(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    database.create_line(cam["id"], "A", 0, 0, 10, 10)
    lc = LineCounter(cam["id"])

    # El mismo track (id 7) se ve como "Auto" (2) tres veces y "Camión" (7)
    # una sola vez (frame ruidoso) antes de cruzar la línea.
    lc._update_votes(_detections(tracker_ids=[7], class_ids=[2]))
    lc._update_votes(_detections(tracker_ids=[7], class_ids=[2]))
    lc._update_votes(_detections(tracker_ids=[7], class_ids=[7]))
    lc._update_votes(_detections(tracker_ids=[7], class_ids=[2]))

    # En el frame del cruce, YOLO confunde momentáneamente la clase (7=camión).
    voted = lc._voted_class(tracker_id=7, fallback_class_id=7)
    assert voted == 2  # la moda ("Auto") gana sobre el frame puntual erróneo


def test_voted_class_falls_back_without_history(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    database.create_line(cam["id"], "A", 0, 0, 10, 10)
    lc = LineCounter(cam["id"])

    assert lc._voted_class(tracker_id=99, fallback_class_id=3) == 3


def test_load_lines_preserves_counters_for_unchanged_lines(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    line_a = database.create_line(cam["id"], "A", 0, 0, 10, 10)

    lc = LineCounter(cam["id"])
    assert len(lc.lines) == 1
    wrapper_a = lc.lines[0]
    wrapper_a.zone._in_count_per_class[0] = 42  # simula cruces ya contados en vivo

    # Agregar una segunda línea no debe resetear el contador de la primera.
    database.create_line(cam["id"], "B", 5, 5, 15, 15)
    lc.load_lines()

    assert len(lc.lines) == 2
    wrapper_a_after = next(w for w in lc.lines if w.id == line_a["id"])
    assert wrapper_a_after is wrapper_a
    assert wrapper_a_after.zone.in_count == 42


def test_load_lines_resets_counter_when_geometry_changes(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    line_a = database.create_line(cam["id"], "A", 0, 0, 10, 10)

    lc = LineCounter(cam["id"])
    wrapper_a = lc.lines[0]
    wrapper_a.zone._in_count_per_class[0] = 42

    database.update_line(line_a["id"], x1=99)
    lc.load_lines()

    wrapper_a_after = lc.lines[0]
    assert wrapper_a_after is not wrapper_a
    assert wrapper_a_after.zone.in_count == 0


def test_load_lines_drops_deleted_lines(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    line_a = database.create_line(cam["id"], "A", 0, 0, 10, 10)
    line_b = database.create_line(cam["id"], "B", 5, 5, 15, 15)

    lc = LineCounter(cam["id"])
    assert len(lc.lines) == 2

    database.delete_line(line_b["id"])
    lc.load_lines()

    assert len(lc.lines) == 1
    assert lc.lines[0].id == line_a["id"]


def _bottom_center_detections(y, tracker_id=1, class_id=2):
    """Detección cuyo anchor BOTTOM_CENTER cae en (50, y)."""
    return _detections(
        tracker_ids=[tracker_id], class_ids=[class_id],
        xyxy=np.array([[40, y - 10, 60, y]], dtype=np.float32),
    )


def test_line_pair_confirms_turn_when_same_tracker_crosses_both_lines(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    entry = database.create_line(cam["id"], "Entrada", 0, 0, 100, 0, movement="GIRO_IZQ")
    exitl = database.create_line(cam["id"], "Salida", 0, 50, 100, 50, movement="GIRO_IZQ")
    database.create_line_pair(cam["id"], "Giro confirmado", "GIRO_IZQ",
                               entry["id"], exitl["id"], max_seconds=15)

    lc = LineCounter(cam["id"])
    assert len(lc.pairs) == 1
    virtual_line_id = lc.pairs[0]["virtual_line_id"]

    # Cruza la línea de entrada (de y=-10 a y=10).
    for y in (-10, -10, 10, 10, 10):
        lc.update(_bottom_center_detections(y))
    assert 1 in lc._pending_pairs  # quedó pendiente para el tracker_id=1

    # El mismo tracker_id cruza después la línea de salida (de y=40 a y=60).
    events = []
    for y in (40, 40, 60, 60, 60):
        events += lc.update(_bottom_center_detections(y))

    assert 1 not in lc._pending_pairs  # se confirmó y se limpió el pendiente
    assert any(e["line_id"] == virtual_line_id for e in events)
    assert lc.get_pair_states()[0]["confirmed"] == 1

    counted = database.query_counts(camera_id=cam["id"], line_id=virtual_line_id)
    assert len(counted) == 1
    assert counted[0]["movement"] == "GIRO_IZQ"


def test_line_pair_does_not_confirm_a_different_tracker(temp_db):
    """Un vehículo distinto cruzando la salida no debe confirmar el par de
    otro tracker_id que nunca cruzó la entrada."""
    cam = database.create_camera("Cam 1", "rtsp://x")
    entry = database.create_line(cam["id"], "Entrada", 0, 0, 100, 0)
    exitl = database.create_line(cam["id"], "Salida", 0, 50, 100, 50)
    database.create_line_pair(cam["id"], "Giro", "GIRO_IZQ", entry["id"], exitl["id"])

    lc = LineCounter(cam["id"])
    # tracker_id=2 cruza directamente la salida sin haber pasado por la entrada.
    events = []
    for y in (40, 40, 60, 60, 60):
        events += lc.update(_bottom_center_detections(y, tracker_id=2))

    assert lc.get_pair_states()[0]["confirmed"] == 0
    assert not any(e["line_name"] == "Giro" for e in events)


def test_deleting_entry_line_also_deletes_dependent_pair(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    entry = database.create_line(cam["id"], "Entrada", 0, 0, 100, 0)
    exitl = database.create_line(cam["id"], "Salida", 0, 50, 100, 50)
    pair = database.create_line_pair(cam["id"], "Giro", "GIRO_IZQ", entry["id"], exitl["id"])
    virtual_line_id = pair["virtual_line_id"]

    database.delete_line(entry["id"])

    assert database.get_line_pair(pair["id"]) is None
    assert database.get_line(virtual_line_id) is None  # línea virtual también se limpia
    assert database.get_line(exitl["id"]) is not None  # la otra línea del par no se toca


def test_line_pair_expires_pending_entry_after_max_seconds(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    entry = database.create_line(cam["id"], "Entrada", 0, 0, 100, 0)
    exitl = database.create_line(cam["id"], "Salida", 0, 50, 100, 50)
    database.create_line_pair(cam["id"], "Giro", "GIRO_IZQ", entry["id"], exitl["id"], max_seconds=15)

    lc = LineCounter(cam["id"])
    for y in (-10, -10, 10, 10, 10):
        lc.update(_bottom_center_detections(y))
    assert 1 in lc._pending_pairs

    # Simula que ya pasó la ventana de 15s sin que cruzara la salida.
    lc._pending_pairs[1][0]["expires_at"] -= 9999
    lc._prune_expired_pairs(time.monotonic())

    assert 1 not in lc._pending_pairs
    assert lc.get_pair_states()[0]["expired"] == 1
    assert lc.get_pair_states()[0]["confirmed"] == 0
