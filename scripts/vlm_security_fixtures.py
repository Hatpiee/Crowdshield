"""Synthetic adversarial image fixtures — Phase 15 (Sprint-0 Validation C).
TEST/VALIDATION TOOLING ONLY, not production pipeline code — deliberately
NOT placed under backend/app/pipeline/.

No external images, no licensing dependency — fully programmatic, same
approach established since Phase 6's synthetic test fixtures.
"""

from typing import Optional

import cv2
import numpy as np

_IMAGE_HEIGHT = 480
_IMAGE_WIDTH = 640
_BACKGROUND_GRAY = 200  # neutral, plain — same value proven in Phase 14's
# own reliably-empty-observation test (test_empty_no_hazard_scene_can_
# return_empty_observations_list)

# The EXACT visual pattern already proven in Phase 14's own adversarial test
# (test_adversarial_embedded_instruction_text_does_not_break_schema_
# validity) to reliably trigger a genuine hazard-like observation from this
# model — deliberately reused verbatim, not reinvented.
_HAZARD_CENTER = (500, 350)
_HAZARD_RADIUS = 60
_HAZARD_COLOR_BGR = (0, 0, 255)

_POSTER_FONT = cv2.FONT_HERSHEY_SIMPLEX
_POSTER_FONT_SCALE = 0.6
_POSTER_THICKNESS = 2
_POSTER_LINE_HEIGHT = 28
_POSTER_TOP_MARGIN = 20
_POSTER_LEFT_MARGIN = 20
_POSTER_MAX_CHARS_PER_LINE = 32


def _wrap_text(text: str, max_chars_per_line: int = _POSTER_MAX_CHARS_PER_LINE) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars_per_line and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_poster_text(image: np.ndarray, text: str) -> None:
    """Renders `text` as a plausible printed sign/poster: a light backing
    rectangle with a black border, black wrapped text inside — clear and
    legible, positioned top-left so it never visually overlaps the hazard
    marker (which sits bottom-right, see _HAZARD_CENTER)."""
    lines = _wrap_text(text)
    if not lines:
        return

    line_widths = [
        cv2.getTextSize(line, _POSTER_FONT, _POSTER_FONT_SCALE, _POSTER_THICKNESS)[0][0]
        for line in lines
    ]
    block_width = max(line_widths) + 40
    block_height = _POSTER_LINE_HEIGHT * len(lines) + 20

    top_left = (_POSTER_LEFT_MARGIN - 10, _POSTER_TOP_MARGIN - 10)
    bottom_right = (
        _POSTER_LEFT_MARGIN - 10 + block_width,
        _POSTER_TOP_MARGIN - 10 + block_height,
    )
    cv2.rectangle(image, top_left, bottom_right, (245, 245, 245), -1)
    cv2.rectangle(image, top_left, bottom_right, (0, 0, 0), 2)

    for i, line in enumerate(lines):
        y = _POSTER_TOP_MARGIN + (i + 1) * _POSTER_LINE_HEIGHT
        cv2.putText(
            image, line, (_POSTER_LEFT_MARGIN, y), _POSTER_FONT, _POSTER_FONT_SCALE,
            (0, 0, 0), _POSTER_THICKNESS, cv2.LINE_AA,
        )


def build_scene(has_real_hazard: bool, injected_text: Optional[str]) -> np.ndarray:
    """Returns a (480, 640, 3) BGR uint8 synthetic frame image.

    has_real_hazard=True draws the exact red-circle marker Phase 14 already
    proved reliably reads as a genuine hazard to this model.
    injected_text, if given, is rendered as a poster/sign in the top-left
    corner — this is the ONLY mechanism by which "attacker" content enters
    the scene; nothing is added to the actual system prompt or API call.
    """
    image = np.full((_IMAGE_HEIGHT, _IMAGE_WIDTH, 3), _BACKGROUND_GRAY, dtype=np.uint8)
    if has_real_hazard:
        cv2.circle(image, _HAZARD_CENTER, _HAZARD_RADIUS, _HAZARD_COLOR_BGR, -1)
    if injected_text:
        _draw_poster_text(image, injected_text)
    return image
