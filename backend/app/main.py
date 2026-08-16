from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.decisions import router as decisions_router
from app.api.evidence import router as evidence_router
from app.api.heatmaps import media_router as heatmap_media_router
from app.api.heatmaps import router as heatmaps_router
from app.api.incidents import router as incidents_router
from app.api.risk import router as risk_router
from app.api.sessions import router as sessions_router
from app.api.system import router as system_router
from app.api.videos import router as videos_router
from app.core.config import settings
from app.core.response import error_envelope, success_envelope

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    # Auth dependencies/routes pass a full error_envelope() dict as `detail` so
    # that the response body matches Phase 1's envelope shape exactly, instead
    # of FastAPI's default {"detail": ...} wrapping.
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    # FastAPI's automatic request validation (e.g. a missing multipart file
    # field) raises this before any route body runs, so it bypasses the
    # HTTPException handler above and would otherwise leak FastAPI's default
    # {"detail": [...]} shape instead of the project's envelope.
    errors = exc.errors()
    if errors:
        loc = " -> ".join(str(part) for part in errors[0].get("loc", []))
        message = f"{loc}: {errors[0].get('msg', 'Invalid request')}" if loc else errors[0].get("msg", "Invalid request")
    else:
        message = "Invalid request"
    return JSONResponse(
        status_code=422,
        content=error_envelope("VALIDATION_ERROR", message),
    )


app.include_router(auth_router, prefix="/api/v1")
app.include_router(videos_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(heatmaps_router, prefix="/api/v1")
app.include_router(heatmap_media_router, prefix="/api/v1")
app.include_router(risk_router, prefix="/api/v1")
app.include_router(evidence_router, prefix="/api/v1")
app.include_router(decisions_router, prefix="/api/v1")
app.include_router(incidents_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1")


@app.get("/health")
def health():
    return success_envelope({"status": "ok", "service": "crowdshield-backend"})
