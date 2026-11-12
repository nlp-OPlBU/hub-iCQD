"""同源网页入口；静态文件路径不依赖启动目录。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
router = APIRouter(include_in_schema=False)


@router.get("/", response_class=FileResponse)
async def homepage() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html", media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )
