"""The web application.

Thin on purpose: the HTTP layer parses the request, enforces the upload limits,
calls the pipeline and turns failures into honest status codes. All the
interesting behaviour lives in :mod:`app.pipeline`, which is why that code can
be tested without a web server.

The application is built by a factory so tests can inject their own settings,
history store and summarizer. That is also what lets the whole HTTP surface be
tested without a language model running.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.models import AnalysisResult
from app.pipeline.orchestrator import Pipeline
from app.pipeline.parser import EmailParseError
from app.pipeline.summarizer import ModelUnavailableError, OllamaClient, Summarizer
from app.store import HistoryStore

STATIC_DIR = Path(__file__).resolve().parent / "static"

# The interface is entirely local: no external scripts, styles, fonts or
# images, and no outbound connections. Saying so in a header means the browser
# enforces it too, so a mistake in the page cannot become a data leak.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

SummarizerFactory = Callable[[Settings], object]


def _default_summarizer(settings: Settings) -> Summarizer:
    return Summarizer(OllamaClient(settings), settings)


def create_app(
    settings: Settings | None = None,
    store: HistoryStore | None = None,
    summarizer_factory: SummarizerFactory = _default_summarizer,
) -> FastAPI:
    """Build the application with its dependencies supplied rather than imported."""
    settings = settings or get_settings()
    store = store or HistoryStore(settings.database_path)

    app = FastAPI(
        title="EmailAssist",
        description="Local-first email thread summariser with a small language model in the loop.",
        version="0.1.0",
    )

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    def get_pipeline() -> Pipeline:
        return Pipeline(summarizer=summarizer_factory(settings), settings=settings)

    # -- analysis -------------------------------------------------------

    @app.post("/api/analyse", response_model=None)
    async def analyse(
        files: list[UploadFile] = File(...),
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> dict:
        """Analyse one or more uploaded .eml files as a single thread."""
        payloads = await _read_uploads(files, settings)

        try:
            result = pipeline.analyse(payloads)
        except EmailParseError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"That does not look like an email: {exc}",
            ) from exc
        except ModelUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc

        entry_id = store.save(result, created_at=datetime.now(timezone.utc))
        return {"id": entry_id} | result.model_dump(mode="json")

    # -- history --------------------------------------------------------

    @app.get("/api/history")
    def list_history() -> dict:
        entries = store.list_recent(limit=settings.history_limit)
        return {
            "entries": [
                {
                    "id": entry.id,
                    "created_at": entry.created_at.isoformat(),
                    "thread_subject": entry.thread_subject,
                    "urgency": entry.result.summary.urgency.value,
                    "suggested_next_step": entry.result.summary.suggested_next_step,
                    "degraded": entry.result.degraded,
                    "flag_count": len(entry.result.security_flags),
                }
                for entry in entries
            ]
        }

    @app.get("/api/history/{entry_id}")
    def get_history_entry(entry_id: int) -> dict:
        entry = store.get(entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such analysis")
        return {
            "id": entry.id,
            "created_at": entry.created_at.isoformat(),
            "result": entry.result.model_dump(mode="json"),
        }

    @app.delete("/api/history/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_history_entry(entry_id: int) -> Response:
        if not store.delete(entry_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such analysis")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/api/history")
    def purge_history() -> dict:
        return {"deleted": store.purge()}

    # -- health ---------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        """Report whether the local model is reachable, for the interface to show."""
        return {
            "status": "ok",
            "model": settings.model_name,
            "model_available": OllamaClient(settings).is_available(),
            "pii_backend": settings.pii_backend,
        }

    # -- interface ------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/about", include_in_schema=False)
    def about() -> FileResponse:
        return FileResponse(STATIC_DIR / "about.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


async def _read_uploads(files: list[UploadFile], settings: Settings) -> list[bytes]:
    """Read the uploaded files, refusing anything past the configured limits.

    The size is checked while reading rather than trusting a declared length,
    so an oversized upload is refused instead of being buffered in full.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No files were uploaded"
        )

    if len(files) > settings.max_attachment_count:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Too many files at once (limit {settings.max_attachment_count})",
        )

    payloads: list[bytes] = []
    total = 0
    for upload in files:
        payload = await upload.read()
        total += len(payload)
        if total > settings.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"The upload is too large "
                    f"(limit {settings.max_upload_bytes // (1024 * 1024)} MB in total)"
                ),
            )
        payloads.append(payload)

    return payloads


app = create_app()


__all__ = ["app", "create_app", "AnalysisResult"]
