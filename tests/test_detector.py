import numpy as np
import supervision as sv

import config
from detector import VehicleDetector, _filter_by_class_confidence


def _make_detections(class_ids, confidences):
    n = len(class_ids)
    return sv.Detections(
        xyxy=np.zeros((n, 4), dtype=np.float32),
        confidence=np.array(confidences, dtype=np.float32),
        class_id=np.array(class_ids, dtype=int),
    )


def test_filter_by_class_confidence_keeps_only_above_class_threshold():
    detections = _make_detections(class_ids=[0, 2, 2], confidences=[0.3, 0.4, 0.6])
    class_confidence = {0: 0.2, 2: 0.5}  # persona 0.2, auto 0.5

    filtered = _filter_by_class_confidence(detections, class_confidence, default_confidence=0.5)

    assert len(filtered) == 2
    assert list(filtered.class_id) == [0, 2]


def test_filter_by_class_confidence_uses_default_for_unlisted_class():
    detections = _make_detections(class_ids=[3], confidences=[0.45])
    filtered = _filter_by_class_confidence(detections, {0: 0.1}, default_confidence=0.5)
    assert len(filtered) == 0  # 0.45 < 0.5 (umbral por defecto, clase 3 no tiene override)


def test_filter_by_class_confidence_noop_without_overrides():
    detections = _make_detections(class_ids=[0, 1], confidences=[0.1, 0.9])
    filtered = _filter_by_class_confidence(detections, {}, default_confidence=0.5)
    assert len(filtered) == 2  # sin overrides, no filtra nada aquí (ya filtró YOLO)


def test_use_openvino_flag(monkeypatch):
    monkeypatch.setattr(config.settings, "device", "cpu")
    assert config.settings.use_openvino is False

    monkeypatch.setattr(config.settings, "device", "openvino")
    assert config.settings.use_openvino is True
    assert config.settings.resolved_device == "cpu"  # sigue siendo "cpu" para torch/device=


def test_create_tracker_uses_configured_params(monkeypatch):
    monkeypatch.setattr(config.settings, "track_activation_threshold", 0.4)
    monkeypatch.setattr(config.settings, "lost_track_buffer", 45)
    monkeypatch.setattr(config.settings, "minimum_consecutive_frames", 3)

    tracker = VehicleDetector.create_tracker()

    assert tracker.track_activation_threshold == 0.4
    assert tracker.max_time_lost == 45  # lost_track_buffer se guarda como max_time_lost
    assert tracker.minimum_consecutive_frames == 3
