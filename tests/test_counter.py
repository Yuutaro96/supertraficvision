import database
from counter import LineCounter


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
