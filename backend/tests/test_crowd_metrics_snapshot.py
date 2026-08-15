"""Phase 21, Step 6: real snapshot persistence + timeseries query tests."""

from app.core.config import settings
from app.pipeline.analysis_orchestrator import AnalysisOrchestrator
from app.services import crowd_metrics_snapshot_service, session_service


def test_snapshot_persisted_at_expected_cadence_during_short_real_run(
    db_session, test_user, monkeypatch, make_processable_video
):
    # A checkpoint fires every 5 frames; with 31 real frames processed,
    # frame_counter hits 5/10/15/20/25/30 -> exactly 6 checkpoints, each
    # with crowd_metrics already available (true from the 2nd frame
    # onward, well before the first checkpoint at frame_counter=5).
    monkeypatch.setattr(settings, "PROGRESS_UPDATE_INTERVAL_FRAMES", 5)
    video = make_processable_video(num_frames=31)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    AnalysisOrchestrator(session.id).run()

    db_session.expire_all()
    snapshots = crowd_metrics_snapshot_service.get_session_crowd_metrics_timeseries(
        db_session, session.id
    )
    assert len(snapshots) == 6
    # frame_number is 0-indexed (MP4FrameSource's own convention) — the
    # 5th/10th/.../30th frame PROCESSED (frame_counter, 1-indexed) is
    # frame_number 4/9/.../29.
    assert [s.frame_number for s in snapshots] == [4, 9, 14, 19, 24, 29]

    timestamps = [s.timestamp_seconds for s in snapshots]
    assert timestamps == sorted(timestamps), "timeseries query must be time-ordered"

    for snapshot in snapshots:
        assert snapshot.session_id == session.id
        assert snapshot.risk_score >= 0.0
        assert snapshot.risk_state is not None
        assert 0.0 <= snapshot.density_confidence <= 1.0


def test_get_latest_snapshot_returns_the_most_recent_row(
    db_session, test_user, monkeypatch, make_processable_video
):
    monkeypatch.setattr(settings, "PROGRESS_UPDATE_INTERVAL_FRAMES", 5)
    video = make_processable_video(num_frames=31)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    AnalysisOrchestrator(session.id).run()

    db_session.expire_all()
    latest = crowd_metrics_snapshot_service.get_latest_snapshot(db_session, session.id)
    all_rows = crowd_metrics_snapshot_service.get_session_crowd_metrics_timeseries(
        db_session, session.id
    )
    assert latest is not None
    assert latest.id == all_rows[-1].id
    assert latest.frame_number == 29


def test_get_snapshot_nearest_timestamp(
    db_session, test_user, monkeypatch, make_processable_video
):
    monkeypatch.setattr(settings, "PROGRESS_UPDATE_INTERVAL_FRAMES", 5)
    video = make_processable_video(num_frames=31)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    AnalysisOrchestrator(session.id).run()

    db_session.expire_all()
    rows = crowd_metrics_snapshot_service.get_session_crowd_metrics_timeseries(
        db_session, session.id
    )
    target_row = rows[2]  # frame_number=14
    nearest = crowd_metrics_snapshot_service.get_snapshot_nearest_timestamp(
        db_session, session.id, target_row.timestamp_seconds + 0.01
    )
    assert nearest is not None
    assert nearest.id == target_row.id


def test_no_snapshots_for_a_session_that_never_ran(db_session, test_user, make_video):
    video = make_video()
    session = session_service.create_session(db_session, video.id, test_user[0].id)

    assert crowd_metrics_snapshot_service.get_latest_snapshot(db_session, session.id) is None
    assert (
        crowd_metrics_snapshot_service.get_session_crowd_metrics_timeseries(db_session, session.id)
        == []
    )
