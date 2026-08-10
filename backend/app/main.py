from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.core.config import settings
from app.core.response import success_envelope

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


app.include_router(auth_router, prefix="/api/v1")


@app.get("/health")
def health():
    return success_envelope({"status": "ok", "service": "crowdshield-backend"})
