from pathlib import Path

import cv2
import numpy as np


# Manual crop presets for known full-frame photos that include background around the card.
# Values are normalized ratios (x1, y1, x2, y2).
MANUAL_CROP_PRESETS = {
    "img_3490.jpg": (0.08, 0.06, 0.92, 0.96),
}


def ensure_image_path_exists(image_path):
    """Return a Path if it exists, otherwise raise FileNotFoundError."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    return path


def apply_manual_crop_preset(image, filename):
    """Apply a known manual crop preset by filename, returning (image, applied)."""
    if image is None or not filename:
        return image, False

    key = str(filename).lower()
    if key not in MANUAL_CROP_PRESETS:
        return image, False

    h, w = image.shape[:2]
    x1r, y1r, x2r, y2r = MANUAL_CROP_PRESETS[key]
    x1 = max(0, min(w - 1, int(round(w * x1r))))
    y1 = max(0, min(h - 1, int(round(h * y1r))))
    x2 = max(x1 + 1, min(w, int(round(w * x2r))))
    y2 = max(y1 + 1, min(h, int(round(h * y2r))))
    cropped = image[y1:y2, x1:x2]
    return cropped, cropped is not None and cropped.size > 0


def detect_and_warp_card(image, width=745, height=1043):
    """Detect the largest card-like quadrilateral and perspective-warp it to portrait size."""
    if image is None:
        return None

    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 45, 140)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        image_area = float(image.shape[0] * image.shape[1])
        best = None
        best_area = 0.0

        for contour in contours:
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if len(approx) != 4:
                continue

            area = cv2.contourArea(approx)
            if area < image_area * 0.08:
                continue

            if area > best_area:
                best_area = area
                best = approx

        if best is None:
            return None

        pts = best.reshape(4, 2)
        pts = sorted(pts, key=lambda point: point[1])
        top_two, bottom_two = pts[:2], pts[2:]
        top_left, top_right = sorted(top_two, key=lambda point: point[0])
        bottom_left, bottom_right = sorted(bottom_two, key=lambda point: point[0])
        rect = np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)

        dst = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )

        matrix = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(image, matrix, (width, height))

        h, w = warped.shape[:2]
        if w > h:
            for _ in range(3):
                warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
                h, w = warped.shape[:2]
                if w <= h:
                    break

        return warped
    except Exception:
        return None
