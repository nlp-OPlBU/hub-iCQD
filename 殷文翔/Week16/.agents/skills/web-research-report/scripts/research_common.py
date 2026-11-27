"""Shared file and URL handling for the research commands."""

import json
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_text(path, content):
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            temporary.replace(path)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 650:
                raise OSError("This location cannot replace files atomically. Use a workspace output path.") from exc
            raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def iso_date(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    except (TypeError, ValueError):
        pass
    raise ValueError(f"Expected a date in YYYY-MM-DD format, got {value!r}.")


def web_url(value):
    if not isinstance(value, str) or any(c.isspace() or c in '<>\\' for c in value):
        raise ValueError("Expected a public HTTP(S) source URL.")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
        raise ValueError("Expected a public HTTP(S) source URL without credentials.")
    parts.port  # Validate the port before callers use this URL.
    return parts
