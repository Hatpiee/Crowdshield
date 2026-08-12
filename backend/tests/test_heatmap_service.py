import pytest

from app.models.heatmap import HeatmapSnapshot, HeatmapType
from app.services import heatmap_service, session_service
from tests.fixtures.crowd_metrics_builder import FRAME_HEIGHT, FRAME_WIDTH, make_crowd_metrics


@pytest.fixture
def make_session(db_session, make_video, test_user):
    def _make_session():
        video = make_video()
        user, _ = test_user
        return session_service.create_session(db_session, video.id, user.id)

    return _make_session


def test_full_crowd_metrics_generates_all_5_types_with_real_files(db_session, make_session):
    session = make_session()
    crowd_metrics = make_crowd_metrics(frame_number=10, timestamp_seconds=0.33, with_projection=True)

    result = heatmap_service.generate_and_persist_heatmaps(
        db_session, session.id, 10, 0.33, crowd_metrics, FRAME_WIDTH, FRAME_HEIGHT
    )

    assert set(result.generated.keys()) == set(HeatmapType)
    assert result.skipped == {}

    storage_dir = heatmap_service.get_storage_dir()
    for heatmap_type, snapshot in result.generated.items():
        assert snapshot.session_id == session.id
        assert snapshot.heatmap_type == heatmap_type
        file_path = storage_dir / snapshot.file_path
        assert file_path.exists(), f"missing file for {heatmap_type}"
        assert file_path.stat().st_size == snapshot.file_size_bytes
        assert snapshot.file_size_bytes > 0

    rows = (
        db_session.query(HeatmapSnapshot)
        .filter(HeatmapSnapshot.session_id == session.id)
        .all()
    )
    assert len(rows) == 5


def test_missing_projection_generates_exactly_4_types_predictive_skipped(db_session, make_session):
    session = make_session()
    crowd_metrics = make_crowd_metrics(frame_number=20, timestamp_seconds=0.66, with_projection=False)

    result = heatmap_service.generate_and_persist_heatmaps(
        db_session, session.id, 20, 0.66, crowd_metrics, FRAME_WIDTH, FRAME_HEIGHT
    )

    assert set(result.generated.keys()) == {
        HeatmapType.DENSITY,
        HeatmapType.PRESSURE,
        HeatmapType.FLOW_CONGESTION,
        HeatmapType.RISK,
    }
    assert list(result.skipped.keys()) == [HeatmapType.PREDICTIVE]
    assert "predictive_projection is None" in result.skipped[HeatmapType.PREDICTIVE]

    predictive_rows = (
        db_session.query(HeatmapSnapshot)
        .filter(
            HeatmapSnapshot.session_id == session.id,
            HeatmapSnapshot.heatmap_type == HeatmapType.PREDICTIVE,
        )
        .all()
    )
    assert predictive_rows == []

    storage_dir = heatmap_service.get_storage_dir()
    predictive_path = storage_dir / f"{session.id}/PREDICTIVE_20.jpg"
    assert not predictive_path.exists()

    all_rows = (
        db_session.query(HeatmapSnapshot)
        .filter(HeatmapSnapshot.session_id == session.id)
        .all()
    )
    assert len(all_rows) == 4


def test_file_naming_collision_free_across_frame_numbers(db_session, make_session):
    session = make_session()
    cm1 = make_crowd_metrics(frame_number=1, timestamp_seconds=0.033, with_projection=True)
    cm2 = make_crowd_metrics(frame_number=2, timestamp_seconds=0.066, with_projection=True)

    result1 = heatmap_service.generate_and_persist_heatmaps(
        db_session, session.id, 1, 0.033, cm1, FRAME_WIDTH, FRAME_HEIGHT
    )
    result2 = heatmap_service.generate_and_persist_heatmaps(
        db_session, session.id, 2, 0.066, cm2, FRAME_WIDTH, FRAME_HEIGHT
    )

    paths_1 = {s.file_path for s in result1.generated.values()}
    paths_2 = {s.file_path for s in result2.generated.values()}
    assert paths_1.isdisjoint(paths_2)

    rows = (
        db_session.query(HeatmapSnapshot)
        .filter(HeatmapSnapshot.session_id == session.id)
        .all()
    )
    assert len(rows) == 10

    storage_dir = heatmap_service.get_storage_dir()
    for row in rows:
        assert (storage_dir / row.file_path).exists()
