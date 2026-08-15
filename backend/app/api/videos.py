from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.core.stream_token import StreamTokenError, generate_stream_token, validate_stream_token
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


@router.get("/{video_id}/stream-token")
def get_stream_token(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Gap 2, Step 4: protected by NORMAL Bearer auth, exactly like every
    # other route — this call obtains the short-lived token; the actual
    # streaming route below is the one a <video> tag hits directly and
    # cannot attach a Bearer header to.
    video = db.get(VideoAsset, video_id)
    if video is None:
        raise HTTPException(
            status_code=404, detail=error_envelope("NOT_FOUND", "Video not found")
        )

    token = generate_stream_token(video_id, current_user.id)
    return success_envelope(
        {
            "stream_token": token,
            "expires_in_seconds": settings.STREAM_TOKEN_EXPIRE_MINUTES * 60,
        }
    )


@router.get("/{video_id}/stream")
def stream_video(video_id: UUID, token: str | None = None, db: Session = Depends(get_db)):
    # Gap 2: deliberately NOT protected by get_current_user — a browser
    # <video> tag issues a plain GET and cannot attach a custom
    # Authorization header. Validated instead via the REQUIRED ?token=
    # query parameter, independently of the main JWT flow.
    #
    # Deliberate, narrow exception to the standard JSON error-envelope
    # convention (documented here, not silently inconsistent): every error
    # response on THIS route is a plain HTTP status with no envelope body
    # — a <video> tag has no way to parse or display a JSON error body
    # regardless of its shape, so there is nothing to gain from one, and a
    # bare status keeps this route's one job (bytes or a clear failure)
    # simple.
    if token is None:
        # A genuinely missing query param would otherwise be FastAPI's own
        # 422 validation response (if `token` were a required str) — made
        # optional here specifically so "no token" collapses into the SAME
        # 401 as "invalid/expired token," matching Gap 2's uniform outcome.
        raise HTTPException(status_code=401)
    try:
        validate_stream_token(token, video_id)
    except StreamTokenError:
        raise HTTPException(status_code=401)

    video = db.get(VideoAsset, video_id)
    if video is None:
        raise HTTPException(status_code=404)

    # storage_filename (the real filesystem path component) is used ONLY
    # server-side here to locate the file — never included in any response
    # body, matching the same non-leaking discipline VideoRead has always
    # applied to its own JSON responses (Phase 3).
    file_path = get_storage_dir() / video.storage_filename
    if not file_path.exists():
        raise HTTPException(status_code=404)

    # Starlette's FileResponse implements real HTTP Range support
    # natively (Accept-Ranges, 206 Partial Content, correct Content-Range,
    # correct partial byte content) — verified directly by
    # test_video_streaming.py's byte-slice-comparison test rather than
    # assumed, per this project's "verify, don't just trust" discipline.
    return FileResponse(path=file_path, media_type=video.mime_type or MP4_CONTENT_TYPE)
