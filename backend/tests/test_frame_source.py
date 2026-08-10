import pytest

from app.pipeline.mp4_frame_source import MP4FrameSource, VideoOpenError
from tests.fixtures.synthetic_video import (
    DEFAULT_FPS,
    DEFAULT_HEIGHT,
    DEFAULT_NUM_FRAMES,
    DEFAULT_WIDTH,
)


def test_frames_yields_expected_count_and_sequential_numbers(synthetic_mp4_path):
    with MP4FrameSource(synthetic_mp4_path) as source:
        frames = list(source.frames())

    assert len(frames) == DEFAULT_NUM_FRAMES
    assert [f.frame_number for f in frames] == list(range(DEFAULT_NUM_FRAMES))


def test_frame_image_has_correct_shape(synthetic_mp4_path):
    with MP4FrameSource(synthetic_mp4_path) as source:
        frames = list(source.frames())

    for frame in frames:
        assert frame.image.shape == (DEFAULT_HEIGHT, DEFAULT_WIDTH, 3)
        assert frame.width == DEFAULT_WIDTH
        assert frame.height == DEFAULT_HEIGHT


def test_get_metadata_returns_correct_values(synthetic_mp4_path):
    with MP4FrameSource(synthetic_mp4_path) as source:
        metadata = source.get_metadata()

    assert metadata.fps == pytest.approx(DEFAULT_FPS)
    assert metadata.frame_count == DEFAULT_NUM_FRAMES
    assert metadata.width == DEFAULT_WIDTH
    assert metadata.height == DEFAULT_HEIGHT
    assert metadata.duration_seconds == pytest.approx(DEFAULT_NUM_FRAMES / DEFAULT_FPS)


def test_frame_step_yields_original_frame_numbers_not_renumbered(synthetic_mp4_path):
    with MP4FrameSource(synthetic_mp4_path, frame_step=2) as source:
        frames = list(source.frames())

    assert len(frames) == DEFAULT_NUM_FRAMES // 2
    assert [f.frame_number for f in frames] == [0, 2, 4, 6, 8]


def test_file_is_not_locked_after_context_exits(tmp_path, synthetic_mp4_path):
    with MP4FrameSource(synthetic_mp4_path) as source:
        list(source.frames())

    # This project has hit real Windows file-locking issues before (an
    # unreleased cv2.VideoCapture can hold the file open). Proving the file
    # can be renamed and deleted right after the `with` block exits is a
    # concrete, OS-level check that VideoCapture.release() actually ran.
    renamed = tmp_path / "renamed.mp4"
    synthetic_mp4_path.rename(renamed)
    renamed.unlink()
    assert not renamed.exists()


def test_opening_corrupt_file_raises(tmp_path):
    corrupt = tmp_path / "not_a_video.mp4"
    corrupt.write_bytes(b"this is definitely not a video file, just text bytes")

    with pytest.raises(VideoOpenError):
        with MP4FrameSource(corrupt):
            pass


def test_opening_empty_file_raises(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    with pytest.raises(VideoOpenError):
        with MP4FrameSource(empty):
            pass


def test_opening_nonexistent_file_raises(tmp_path):
    missing = tmp_path / "does_not_exist.mp4"

    with pytest.raises(VideoOpenError):
        with MP4FrameSource(missing):
            pass
