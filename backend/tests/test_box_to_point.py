import pytest

from app.pipeline.box_to_point import (
    HEAD_SCALE_FRACTION,
    HEAD_Y_FRACTION,
    collapse_box_to_point,
)


def test_collapse_box_to_point_matches_hand_calculated_formula():
    # box = (x1=0, y1=0, x2=100, y2=200)
    # box_width = 100 - 0 = 100
    # box_height = 200 - 0 = 200
    # head_x = 0 + 100 / 2 = 50
    # head_y = 0 + 0.10 * 200 = 20
    # local_scale = 0.13 * 200 = 26
    point, local_scale = collapse_box_to_point((0.0, 0.0, 100.0, 200.0))

    assert point.x == pytest.approx(50.0)
    assert point.y == pytest.approx(20.0)
    assert local_scale == pytest.approx(26.0)


def test_collapse_box_to_point_with_nonzero_origin():
    # box = (x1=40, y1=60, x2=140, y2=360)
    # box_width = 140 - 40 = 100
    # box_height = 360 - 60 = 300
    # head_x = 40 + 100 / 2 = 90
    # head_y = 60 + 0.10 * 300 = 90
    # local_scale = 0.13 * 300 = 39
    point, local_scale = collapse_box_to_point((40.0, 60.0, 140.0, 360.0))

    assert point.x == pytest.approx(90.0)
    assert point.y == pytest.approx(90.0)
    assert local_scale == pytest.approx(39.0)


def test_documented_constants_match_spec():
    assert HEAD_Y_FRACTION == 0.10
    assert HEAD_SCALE_FRACTION == 0.13
