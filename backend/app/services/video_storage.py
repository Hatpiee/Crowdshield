import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import REPO_ROOT, settings

MP4_EXTENSION = ".mp4"
MP4_CONTENT_TYPE = "video/mp4"

# ISO base media file format (MP4) files start with a box whose 4-byte size
# field is immediately followed by a 4-byte type field. For a valid MP4 the
# very first box is always "ftyp", so its ASCII bytes land at offset 4-7.
FTYP_MARKER = b"ftyp"
FTYP_OFFSET = 4

CHUNK_SIZE = 1024 * 1024  # 1MB, streamed and checked against the size limit


class UploadTooLargeError(Exception):
    pass


def get_storage_dir() -> Path:
    # VIDEO_STORAGE_PATH may be relative (the real "storage/videos" default,
    # anchored to the repo root — same fix as the .env path in Phase 2) or
    # already absolute (as tests set it, pointed at a temp directory).
    raw = Path(settings.VIDEO_STORAGE_PATH)
    path = raw if raw.is_absolute() else REPO_ROOT / raw
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_storage_filename(original_filename: str) -> str:
    # Only .mp4 is ever accepted (frozen V1 scope), so the stored extension
    # is always lowercase ".mp4" regardless of the original filename's
    # casing (e.g. "Video.MP4"). The name itself is a fresh uuid4 — never
    # derived from the client-supplied filename — to prevent path traversal
    # and collisions (decision #2, master spec §30).
    return f"{uuid.uuid4()}{MP4_EXTENSION}"


def validate_mp4_magic_bytes(file_header: bytes) -> bool:
    if len(file_header) < FTYP_OFFSET + len(FTYP_MARKER):
        return False
    return file_header[FTYP_OFFSET : FTYP_OFFSET + len(FTYP_MARKER)] == FTYP_MARKER


async def stream_upload_to_disk(
    upload_file: UploadFile, destination: Path, max_size_bytes: int
) -> int:
    """Streams `upload_file` to `destination` in chunks, tracking cumulative
    bytes written rather than trusting Content-Length. Aborts and deletes the
    partial file the instant the running total would exceed max_size_bytes."""
    bytes_written = 0
    try:
        with open(destination, "wb") as out_file:
            while True:
                chunk = await upload_file.read(CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_size_bytes:
                    raise UploadTooLargeError(
                        f"Upload exceeds the {max_size_bytes}-byte limit"
                    )
                out_file.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return bytes_written
