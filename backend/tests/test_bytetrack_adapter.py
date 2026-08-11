import pytest

from app.core.config import settings
from app.pipeline.box_to_point import collapse_box_to_point
from app.pipeline.bytetrack_adapter import ByteTrackAdapter
from app.pipeline.detection import Detection, DetectionResult, Point

FPS = 30.0


def _make_detection(box: tuple[float, float, float, float], confidence: float = 0.9) -> Detection:
    point, local_scale = collapse_box_to_point(box)
    return Detection(point=point, local_scale=local_scale, confidence=confidence, raw_box=box)


def _make_detection_result(
    frame_number: int,
    boxes: list[tuple[float, float, float, float]],
    fps: float = FPS,
) -> DetectionResult:
    return DetectionResult(
        frame_number=frame_number,
        timestamp_seconds=frame_number / fps,
        detections=[_make_detection(b) for b in boxes],
        model_name="yolo11n",
        confidence_threshold_used=0.25,
    )


@pytest.fixture(autouse=True)
def _confirm_tracks_immediately(monkeypatch):
    # Isolates these tests to tracking/velocity/correspondence correctness
    # rather than mixing in minimum_consecutive_frames' separate
    # confirmation-delay behavior (a real, independent library feature —
    # the production default of 2 is untouched by this fixture; it only
    # applies inside this test module).
    monkeypatch.setattr(settings, "TRACKER_MINIMUM_CONSECUTIVE_FRAMES", 1)


def test_tracking_continuity_and_velocity_on_straight_line_motion():
    adapter = ByteTrackAdapter(fps=FPS)

    # Single object, x increasing by 10px/frame, fixed y=100, 50x100 box.
    # At FPS=30, dt between consecutive frames = 1/30 s.
    # velocity_x = 10px / (1/30 s) = 300 px/s ; velocity_y = 0 px/s
    # speed = sqrt(300^2 + 0^2) = 300 px/s
    #
    # NOTE, verified empirically (not assumed): even with
    # TRACKER_MINIMUM_CONSECUTIVE_FRAMES=1, ByteTrackTracker never confirms
    # a brand-new track on the very frame it's created — confirmation
    # always lags by exactly one additional matched frame. So frame 0
    # yields zero surfaced tracks (tracker_id was the library's internal
    # -1, filtered out), and the track's first-ever appearance in our
    # output is frame 1, with only one real trajectory point so far.
    results = []
    for frame_number in range(6):
        x1 = 100.0 + frame_number * 10.0
        box = (x1, 100.0, x1 + 50.0, 200.0)
        results.append(adapter.update(_make_detection_result(frame_number, [box])))

    assert results[0].tracks == [], "not yet confirmed on its very first frame"
    assert results[0].tracker_name == "bytetrack"

    assert len(results[1].tracks) == 1
    track_id = results[1].tracks[0].track_id
    # Only one real trajectory point recorded so far -> None, never
    # fabricated as zero (decision #4).
    assert results[1].tracks[0].velocity is None
    assert results[1].tracks[0].speed is None

    # Frame 2 onward: two-or-more real trajectory points exist -> matches
    # the hand-calculated motion, and the same track_id persists.
    for result in results[2:]:
        assert len(result.tracks) == 1
        track = result.tracks[0]
        assert track.track_id == track_id
        assert track.velocity == pytest.approx((300.0, 0.0))
        assert track.speed == pytest.approx(300.0)


def test_trajectory_bounded_to_configured_history_length(monkeypatch):
    monkeypatch.setattr(settings, "TRACK_HISTORY_LENGTH", 5)
    adapter = ByteTrackAdapter(fps=FPS)

    num_updates = 12
    last_result = None
    for frame_number in range(num_updates):
        x1 = 100.0 + frame_number * 10.0
        box = (x1, 100.0, x1 + 50.0, 200.0)
        last_result = adapter.update(_make_detection_result(frame_number, [box]))

    trajectory = last_result.tracks[0].trajectory
    assert len(trajectory) == 5
    # The most recent 5 entries are retained, not the oldest.
    frame_numbers = [entry[0] for entry in trajectory]
    assert frame_numbers == list(range(num_updates - 5, num_updates))


def test_correspondence_with_two_simultaneous_detections():
    adapter = ByteTrackAdapter(fps=FPS)

    box_a = (0.0, 0.0, 20.0, 40.0)  # top-left corner
    box_b = (500.0, 500.0, 540.0, 620.0)  # far away, non-adjacent

    # Frame 0 only creates unconfirmed (tracker_id=-1) tracks — see the
    # confirmation-lag note in the straight-line-motion test above. Feed
    # the same stationary boxes again on frame 1 so both are confirmed.
    adapter.update(_make_detection_result(0, [box_a, box_b]))
    result = adapter.update(_make_detection_result(1, [box_a, box_b]))
    assert len(result.tracks) == 2

    point_a, scale_a = collapse_box_to_point(box_a)
    point_b, scale_b = collapse_box_to_point(box_b)

    by_point = {(round(t.point.x, 3), round(t.point.y, 3)): t for t in result.tracks}

    track_a = by_point[(round(point_a.x, 3), round(point_a.y, 3))]
    track_b = by_point[(round(point_b.x, 3), round(point_b.y, 3))]

    assert track_a.local_scale == pytest.approx(scale_a)
    assert track_b.local_scale == pytest.approx(scale_b)
    assert track_a.track_id != track_b.track_id


def test_empty_detections_does_not_error():
    adapter = ByteTrackAdapter(fps=FPS)
    result = adapter.update(_make_detection_result(0, []))

    assert result.tracks == []
    assert result.source_detection_count == 0


def test_lost_track_reappears_as_is_lost_with_last_known_position():
    # Investigation finding (decision #3): lost tracks ARE exposed by this
    # installed library version — via the tracker's internal `tracks` list,
    # not update()'s return value. This test proves the adapter surfaces
    # them correctly, with the LAST REAL position (never an extrapolated
    # new one).
    adapter = ByteTrackAdapter(fps=FPS)

    last_box = None
    result = None
    for frame_number in range(3):
        x1 = 100.0 + frame_number * 10.0
        last_box = (x1, 100.0, x1 + 50.0, 200.0)
        result = adapter.update(_make_detection_result(frame_number, [last_box]))
    track_id = result.tracks[0].track_id
    last_point, last_scale = collapse_box_to_point(last_box)

    # Simulate a brief miss: no detections this frame.
    lost_result = adapter.update(_make_detection_result(3, []))

    assert len(lost_result.tracks) == 1
    lost_track = lost_result.tracks[0]
    assert lost_track.track_id == track_id
    assert lost_track.is_lost is True
    assert lost_track.frames_since_last_matched == 1
    assert lost_track.point.x == pytest.approx(last_point.x)
    assert lost_track.point.y == pytest.approx(last_point.y)
    assert lost_track.local_scale == pytest.approx(last_scale)


def test_two_adapters_do_not_share_track_id_state():
    adapter_1 = ByteTrackAdapter(fps=FPS)
    adapter_2 = ByteTrackAdapter(fps=FPS)

    box = (100.0, 100.0, 150.0, 200.0)
    # Two calls each, since confirmation lags one frame (see straight-line
    # motion test's note).
    adapter_1.update(_make_detection_result(0, [box]))
    adapter_2.update(_make_detection_result(0, [box]))
    result_1 = adapter_1.update(_make_detection_result(1, [box]))
    result_2 = adapter_2.update(_make_detection_result(1, [box]))

    # A shared/global id counter would make the second adapter continue
    # from wherever the first left off; independent instances both start
    # fresh and assign the same first id.
    assert result_1.tracks[0].track_id == result_2.tracks[0].track_id


def test_detection_with_none_raw_box_raises():
    adapter = ByteTrackAdapter(fps=FPS)
    bad_detection = Detection(
        point=Point(x=1.0, y=1.0), local_scale=1.0, confidence=0.9, raw_box=None
    )
    bad_result = DetectionResult(
        frame_number=0,
        timestamp_seconds=0.0,
        detections=[bad_detection],
        model_name="yolo11n",
        confidence_threshold_used=0.25,
    )

    with pytest.raises(ValueError):
        adapter.update(bad_result)


def test_nonpositive_fps_raises():
    with pytest.raises(ValueError):
        ByteTrackAdapter(fps=0)
    with pytest.raises(ValueError):
        ByteTrackAdapter(fps=-5.0)
