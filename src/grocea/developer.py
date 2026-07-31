from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse

from grocea.devlogs import api_log_buffer

WEB_DIRECTORY = Path(__file__).with_name("web")

router = APIRouter(include_in_schema=False)


def web_file(name: str, media_type: str) -> FileResponse:
    return FileResponse(
        WEB_DIRECTORY / name,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/")
def landing_page() -> FileResponse:
    return web_file("index.html", "text/html; charset=utf-8")


@router.get("/logs")
def logs_page() -> FileResponse:
    return web_file("logs.html", "text/html; charset=utf-8")


@router.get("/developer-assets/developer.css")
def developer_styles() -> FileResponse:
    return web_file("developer.css", "text/css; charset=utf-8")


@router.get("/developer-assets/logs.js")
def developer_script() -> FileResponse:
    return web_file("logs.js", "text/javascript; charset=utf-8")


@router.get("/api/dev/logs")
def api_logs(
    response: Response,
    stream_id: UUID | None = None,
    after_id: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return api_log_buffer.snapshot(stream_id=stream_id, after_id=after_id).to_payload()
