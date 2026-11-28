"""Render a Markdown report from an agent-reviewed evidence brief."""

import argparse
import re
from pathlib import Path

from research_common import iso_date, read_json, web_url, write_text

LABELS = {"fact": "", "analysis": "分析", "recommendation": "建议", "limitation": "资料限制"}


def plain_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty plain text in the report brief.")
    text = " ".join(value.split())
    return re.sub(r"([\\\x60*_{}\[\]<>|])", r"\\\1", text)


def render(brief):
    lines = [f"# {plain_text(brief['title'])}", ""]
    if scope := brief.get("scope"):
        lines.extend([plain_text(scope), ""])
    sources = brief["sources"]
    for source_id, source in sources.items():
        plain_text(source["title"])
        web_url(source["url"])
        if source.get("published"):
            iso_date(source["published"])
        if not source_id.strip():
            raise ValueError("Source IDs cannot be empty.")
    if not brief["sections"]:
        raise ValueError("Provide at least one report section.")

    used = {}
    for section in brief["sections"]:
        lines.extend([f"## {plain_text(section['title'])}", ""])
        if not section["items"]:
            raise ValueError("Report sections cannot be empty.")
        for item in section["items"]:
            kind = item["kind"]
            if kind not in LABELS:
                raise ValueError(f"Unsupported report item kind {kind!r}.")
            ids = item.get("sources", [])
            if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
                raise ValueError("Each item's sources must be a list of source IDs.")
            if kind in {"fact", "analysis"} and not ids:
                raise ValueError(f"An item of kind {kind!r} needs a source.")
            refs = []
            for source_id in dict.fromkeys(ids):
                if source_id not in sources:
                    raise ValueError(f"Unknown source ID {source_id!r}.")
                number = used.setdefault(source_id, len(used) + 1)
                refs.append(f"[〔{number}〕](<{sources[source_id]['url']}>)")
            text = plain_text(item["text"])
            label = LABELS[kind]
            prefix = f"**{label}** " if label else ""
            lines.extend([f"{prefix}{text}" + (" " + " ".join(refs) if refs else ""), ""])

    if used:
        lines.extend(["## 来源", ""])
        for source_id, number in used.items():
            source = sources[source_id]
            title = plain_text(source["title"])
            published = f"（{source['published']}）" if source.get("published") else ""
            lines.append(f"{number}. [{title}](<{source['url']}>) {published}".rstrip())
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    try:
        write_text(options.output, render(read_json(options.brief)))
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as exc:
        parser.error(f"Unable to render report. {exc}")


if __name__ == "__main__":
    main()
