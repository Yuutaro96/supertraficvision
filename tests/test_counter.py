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
