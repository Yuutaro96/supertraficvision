import database


def test_camera_crud(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x", True)
    assert cam["id"] > 0
    assert database.get_camera(cam["id"])["name"] == "Cam 1"

    updated = database.update_camera(cam["id"], name="Cam 1 editada")
    assert updated["name"] == "Cam 1 editada"
    assert updated["url"] == "rtsp://x"  # no se tocó

    assert database.delete_camera(cam["id"]) is True
    assert database.get_camera(cam["id"]) is None


def test_line_zero_coordinate_is_kept(temp_db):
    """update_line no debe tratar 0 como 'sin cambio' (bug clásico de truthiness)."""
    cam = database.create_camera("Cam 1", "rtsp://x")
    line = database.create_line(cam["id"], "L1", x1=10, y1=10, x2=20, y2=20)

    updated = database.update_line(line["id"], x1=0, y1=0)
    assert updated["x1"] == 0
    assert updated["y1"] == 0
    assert updated["x2"] == 20  # no enviado, se mantiene


def test_unsynced_counts_roundtrip(temp_db):
    cam = database.create_camera("Cam 1", "rtsp://x")
    line = database.create_line(cam["id"], "L1", 0, 0, 10, 10)

    database.record_count(cam["id"], line["id"], "Auto", "IN")
    database.record_count(cam["id"], line["id"], "Moto", "OUT")

    pending = database.get_unsynced_counts()
    assert len(pending) == 2
    assert {c["class_name"] for c in pending} == {"Auto", "Moto"}

    database.mark_synced([pending[0]["id"]])
    pending_after = database.get_unsynced_counts()
    assert len(pending_after) == 1
    assert pending_after[0]["id"] == pending[1]["id"]


def test_settings_persist_roundtrip(temp_db):
    database.save_settings({"confidence": 0.7, "model_size": "s"})
    database._load_settings()
    import config as cfg
    assert cfg.settings.confidence == 0.7
    assert cfg.settings.model_size == "s"
