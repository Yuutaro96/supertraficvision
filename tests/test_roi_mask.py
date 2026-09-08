import cv2
import numpy as np


def apply_roi(frame, points):
    """Misma lógica que CameraStream._apply_roi, aislada para poder testear
    la mecánica de máscara sin construir un CameraStream completo (que
    requiere detector/GPU)."""
    if not points:
        return frame
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
    cv2.fillPoly(mask, [pts], 255)
    return cv2.bitwise_and(frame, frame, mask=mask)


def test_roi_blacks_out_pixels_outside_polygon():
    frame = np.full((100, 100, 3), 200, dtype=np.uint8)
    points = [[10, 10], [50, 10], [50, 50], [10, 50]]

    masked = apply_roi(frame, points)

    assert masked[30, 30].tolist() == [200, 200, 200]  # dentro del polígono
    assert masked[90, 90].tolist() == [0, 0, 0]         # fuera del polígono


def test_roi_noop_without_points():
    frame = np.full((50, 50, 3), 123, dtype=np.uint8)
    assert np.array_equal(apply_roi(frame, []), frame)
