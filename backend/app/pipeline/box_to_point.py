"""Box-to-point collapse — the master spec's own distinct architectural
step ("Box-to-point collapse: Deterministic, applied at detector output
boundary"). Converts a full-body detection box into the head-anchored
point that is this system's official output representation (§9).

The constants below are engineering judgment, NOT empirically validated
or peer-reviewed — flagged the same way the master spec flags its own
unvalidated thresholds (e.g. Crowd Pressure). They are a candidate for
future calibration against real footage.

Kept as its own pure-function module (no model, no image, no I/O) so it
is independently unit-testable with plain synthetic coordinates.
"""

from app.pipeline.detection import Point

# Approximates the head's vertical center rather than the box's raw top
# edge, which often includes empty margin above the actual head.
HEAD_Y_FRACTION = 0.10

# Anthropometric approximation: head height is roughly 1/7 to 1/8 of
# standing body height; box_height approximates standing height for a
# full-body detection box.
HEAD_SCALE_FRACTION = 0.13


def collapse_box_to_point(
    box: tuple[float, float, float, float],
) -> tuple[Point, float]:
    """box is (x1, y1, x2, y2) in pixel coordinates. Returns
    (head_point, local_scale)."""
    x1, y1, x2, y2 = box
    box_width = x2 - x1
    box_height = y2 - y1

    head_x = x1 + box_width / 2
    head_y = y1 + HEAD_Y_FRACTION * box_height
    local_scale = HEAD_SCALE_FRACTION * box_height

    return Point(x=head_x, y=head_y), local_scale
