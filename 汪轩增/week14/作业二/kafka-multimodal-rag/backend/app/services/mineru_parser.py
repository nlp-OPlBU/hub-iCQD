"""调用 MinerU 命令行解析 PDF。

MinerU 单文件解析通常需要几十秒到数分钟，所以一定放在 Kafka 消费者里慢慢做，
不能放在 HTTP 请求链路里。
"""

import logging
import shlex
import subprocess
from pathlib import Path

from ..config import Settings

logger = logging.getLogger(__name__)


class MinerUError(RuntimeError):
    pass


def parse_pdf(pdf_path: str | Path, output_root: str | Path, settings: Settings) -> Path:
    """解析 PDF，返回生成的 markdown 文件路径。"""
    pdf_path = Path(pdf_path)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    if not pdf_path.is_file():
        raise MinerUError(f"PDF 文件不存在: {pdf_path}")

    cmd = shlex.split(settings.mineru_cmd)
    cmd += [
        "-p", str(pdf_path),
        "-o", str(output_root),
        "-b", settings.mineru_backend,
    ]
    if settings.mineru_backend.endswith("http-client") and settings.mineru_server_url:
        cmd += ["-u", settings.mineru_server_url]
    if settings.mineru_extra_args.strip():
        cmd += shlex.split(settings.mineru_extra_args)

    logger.info("Running MinerU: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.mineru_timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MinerUError(
            f"找不到 MinerU 命令 {settings.mineru_cmd!r}，请确认已安装并在 PATH 中"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MinerUError(
            f"MinerU 解析超时（>{settings.mineru_timeout_seconds}s）"
        ) from exc

    if proc.stdout:
        logger.info("MinerU stdout: %s", proc.stdout[-2000:])
    if proc.returncode != 0:
        raise MinerUError(
            f"MinerU 解析失败（exit={proc.returncode}）: {(proc.stderr or '')[-2000:]}"
        )

    # MinerU 不同版本的输出目录结构略有差别：
    #   <output_root>/<pdf_stem>/auto/<name>.md
    #   <output_root>/<pdf_stem>/vlm/<name>.md
    # 这里直接递归找体积最大的 md，兼容各种版本。
    candidates = [
        p for p in (output_root / pdf_path.stem).rglob("*.md") if p.is_file()
    ]
    if not candidates:
        raise MinerUError(
            f"MinerU 执行成功但没找到 markdown 文件，请检查输出目录: {output_root / pdf_path.stem}"
        )

    markdown = max(candidates, key=lambda p: p.stat().st_size)
    logger.info("MinerU markdown: %s", markdown)
    return markdown
