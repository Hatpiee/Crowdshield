from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.user import User
from app.models.video import VideoAsset
from app.schemas.video import VideoListResponse, VideoRead
from app.services.video_metadata_service import UnreadableVideoError, extract_metadata
from app.services.video_storage import (
    MP4_CONTENT_TYPE,
    MP4_EXTENSION,
    UploadTooLargeError,
    generate_storage_filename,
    get_storage_dir,
    stream_upload_to_disk,
    validate_mp4_magic_bytes,
)

router = APIRouter(prefix="/videos", tags=["videos"])

MAGIC_BYTES_READ_LEN = 12


def _invalid_file_type(message: str) -> HTTPException:
    return HTTPException(
        status_code=400, detail=error_envelope("INVALID_FILE_TYPE", message)
    )


def _to_video_read(video: VideoAsset, uploader_email: str) -> VideoRead:
    return VideoRead(
        id=video.id,
        original_filename=video.original_filename,
        file_size_bytes=video.file_size_bytes,
        mime_type=video.mime_type,
        uploaded_by=video.uploaded_by,
        uploaded_by_email=uploader_email,
        created_at=video.created_at,
        fps=video.fps,
        duration_seconds=video.duration_seconds,
        frame_count=video.frame_count,
        width=video.width,
        height=video.height,
    )


@router.post("", status_code=201)
async def upload_video(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(MP4_EXTENSION):
        raise _invalid_file_type("Only .mp4 files are accepted")

    if file.content_type != MP4_CONTENT_TYPE:
        raise _invalid_file_type(f"Content-Type must be {MP4_CONTENT_TYPE}")

    storage_dir = get_storage_dir()
    storage_filename = generate_storage_filename(file.filename)
    destination = storage_dir / storage_filename

    max_size_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    try:
        file_size = await stream_upload_to_disk(file, destination, max_size_bytes)
    except UploadTooLargeError:
        raise HTTPException(
            status_code=400,
            detail=error_envelope(
                "FILE_TOO_LARGE",
                f"Upload exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB limit",
            ),
        )

    with open(destination, "rb") as f:
        header = f.read(MAGIC_BYTES_READ_LEN)
    if not validate_mp4_magic_bytes(header):
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=error_envelope(
                "INVALID_MP4_CONTENT", "File content is not a valid MP4"
            ),
        )

    # The magic-byte check only proves the file starts with a plausible MP4
    # header — it doesn't prove OpenCV can actually decode it. Opening it for
    # real is the only way to catch a file that's spoofed just well enough to
    # pass the cheap check above but isn't a genuinely playable video.
    try:
        metadata = extract_metadata(destination)
    except UnreadableVideoError:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=error_envelope(
                "UNREADABLE_VIDEO", "The uploaded file could not be read as a valid video"
            ),
        )

    video = VideoAsset(
        original_filename=file.filename,
        storage_filename=storage_filename,
        file_size_bytes=file_size,
        mime_type=file.content_type,
        uploaded_by=current_user.id,
        fps=metadata.fps,
        duration_seconds=metadata.duration_seconds,
        frame_count=metadata.frame_count,
        width=metadata.width,
        height=metadata.height,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    video_read = _to_video_read(video, current_user.email)
    return success_envelope(video_read.model_dump(mode="json"))


@router.get("")
def list_videos(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total = db.query(func.count(VideoAsset.id)).scalar()
    rows = (
        db.query(VideoAsset, User.email)
        .join(User, VideoAsset.uploaded_by == User.id)
        .order_by(VideoAsset.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    items = [_to_video_read(video, email) for video, email in rows]
    response = VideoListResponse(items=items, total=total, limit=limit, offset=offset)
    return success_envelope(response.model_dump(mode="json"))


@router.get("/{video_id}")
def get_video(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(VideoAsset, User.email)
        .join(User, VideoAsset.uploaded_by == User.id)
        .filter(VideoAsset.id == video_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("NOT_FOUND", "Video not found"),
        )
    video, email = row
    return success_envelope(_to_video_read(video, email).model_dump(mode="json"))
