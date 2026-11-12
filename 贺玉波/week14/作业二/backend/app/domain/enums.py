"""领域枚举：只依赖标准库，可被任意层引用。"""

from __future__ import annotations

from enum import Enum


class DocStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    DONE = "done"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {DocStatus.DONE, DocStatus.FAILED}

    @property
    def in_progress(self) -> bool:
        return self in {DocStatus.PARSING, DocStatus.CHUNKING, DocStatus.INDEXING}


class ChunkKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    FIGURE = "figure"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
