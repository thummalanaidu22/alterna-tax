"""
Heading-to-pixel math and marker drawing for street-view property localization.
"""

import math
from typing import Optional

import cv2
import numpy as np


def heading_to_pixel_x(
    center_heading: float,
    view_heading: float,
    fov: float,
    canvas_width: int,
) -> Optional[int]:
    rel = ((center_heading - view_heading + 180.0) % 360.0) - 180.0
    if abs(rel) > fov / 2.0:
        return None
    return int(round(canvas_width * (rel + fov / 2.0) / fov))


def draw_property_marker(
    img: np.ndarray,
    x_px: int,
    label: str = "TARGET",
) -> np.ndarray:
    h, w = img.shape[:2]
    x_px = max(14, min(w - 14, x_px))

    RED   = (30,  30, 220)
    WHITE = (255, 255, 255)
    BLACK = (0,   0,   0)

    out = img.copy()

    # Semi-transparent vertical guide line (wider, more visible)
    overlay = out.copy()
    cv2.line(overlay, (x_px, 0), (x_px, h - 1), RED, 2, cv2.LINE_AA)
    out = cv2.addWeighted(out, 0.72, overlay, 0.28, 0)

    # Diamond at 35 % height — larger radius scales with image height
    dy = int(h * 0.35)
    r  = max(18, min(28, h // 30))   # was h//55 (too small), now h//30
    pts = np.array([
        [x_px,     dy - r],
        [x_px + r, dy    ],
        [x_px,     dy + r],
        [x_px - r, dy    ],
    ], dtype=np.int32)
    # Black shadow for contrast on any background
    cv2.polylines(out, [pts], isClosed=True, color=BLACK, thickness=4, lineType=cv2.LINE_AA)
    # Red diamond outline (not filled — facade stays visible)
    cv2.polylines(out, [pts], isClosed=True, color=RED,   thickness=2, lineType=cv2.LINE_AA)

    # Crosshair ticks outside the diamond
    tick = max(10, r // 2)
    for seg in [
        ((x_px - r - tick, dy), (x_px - r - 1, dy)),
        ((x_px + r + 1,    dy), (x_px + r + tick, dy)),
        ((x_px,   dy - r - tick), (x_px, dy - r - 1)),
    ]:
        cv2.line(out, seg[0], seg[1], BLACK, 3, cv2.LINE_AA)
        cv2.line(out, seg[0], seg[1], RED,   2, cv2.LINE_AA)

    # Center dot
    cv2.circle(out, (x_px, dy), 3, WHITE, -1, cv2.LINE_AA)
    cv2.circle(out, (x_px, dy), 3, RED,   -1, cv2.LINE_AA)

    # Label — larger font
    font_scale = max(0.45, min(0.60, h / 1400))
    lx, ly = x_px + r + 8, dy - 4
    cv2.putText(out, label, (lx + 1, ly + 1), cv2.FONT_HERSHEY_SIMPLEX, font_scale, BLACK, 3, cv2.LINE_AA)
    cv2.putText(out, label, (lx,     ly    ), cv2.FONT_HERSHEY_SIMPLEX, font_scale, WHITE, 1, cv2.LINE_AA)

    return out


def draw_offscreen_arrow(img: np.ndarray, direction: str, label: str = "TARGET") -> np.ndarray:
    h, w = img.shape[:2]
    RED, WHITE, BLACK = (30, 30, 220), (255, 255, 255), (0, 0, 0)
    out = img.copy()
    margin, aw, ah = 18, 20, 13
    cy = int(h * 0.35)

    if direction == "left":
        pts = np.array([[margin, cy], [margin + aw, cy - ah], [margin + aw, cy + ah]], dtype=np.int32)
        tx = margin + aw + 5
    else:
        pts = np.array([[w - margin, cy], [w - margin - aw, cy - ah], [w - margin - aw, cy + ah]], dtype=np.int32)
        tx = w - margin - aw - 80

    cv2.fillPoly(out, [pts], RED)
    cv2.polylines(out, [pts], isClosed=True, color=WHITE, thickness=1, lineType=cv2.LINE_AA)
    font_scale = max(0.30, min(0.40, h / 2500))
    cv2.putText(out, label, (tx + 1, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, font_scale, BLACK, 2, cv2.LINE_AA)
    cv2.putText(out, label, (tx,     cy + 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale, WHITE, 1, cv2.LINE_AA)
    return out


def apply_view_marker(
    img: np.ndarray,
    center_heading: float,
    view_heading: float,
    fov: float,
    label: str = "TARGET",
) -> tuple:
    x_px = heading_to_pixel_x(center_heading, view_heading, fov, img.shape[1])
    if x_px is not None:
        return draw_property_marker(img, x_px, label=label), x_px
    rel = ((center_heading - view_heading + 180.0) % 360.0) - 180.0
    return draw_offscreen_arrow(img, "right" if rel < 0 else "left", label=label), None
