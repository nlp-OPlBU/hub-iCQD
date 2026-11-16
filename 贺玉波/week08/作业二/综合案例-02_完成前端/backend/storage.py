# -*- coding: utf-8 -*-
"""研究报告落盘：backend/data/research/{research_id}.json，一个研究一个文件。

状态流转 pending -> running -> completed / failed，每次变化整文件覆盖写。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from . import config
from .models import ResearchRecord, Status

_LOCK = threading.Lock()


def _path(rid: str):
    config.ensure_data_dir()
    return config.DATA_DIR / f"{rid}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(rid: str, topic: str) -> ResearchRecord:
    """创建一条 pending 状态的记录并落盘。"""
    rec = ResearchRecord(
        research_id=rid,
        topic=topic,
        status=Status.pending,
        created_at=_now(),
        updated_at=_now(),
    )
    save(rec)
    return rec


def save(rec: ResearchRecord) -> None:
    """整文件覆盖写入一条记录。"""
    with _LOCK:
        _path(rec.research_id).write_text(
            rec.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def update_status(rid: str, status: str, error: str | None = None) -> None:
    """更新记录状态（可选附带错误信息）。"""
    rec = get(rid)
    if rec is None:
        return
    rec.status = Status(status)
    rec.updated_at = _now()
    if error:
        rec.error = error
    save(rec)


def get(rid: str) -> ResearchRecord | None:
    """按 research_id 读取记录，不存在返回 None。"""
    p = _path(rid)
    if not p.exists():
        return None
    return ResearchRecord.model_validate_json(p.read_text(encoding="utf-8"))


def list_all() -> list[ResearchRecord]:
    """按创建时间返回全部记录。"""
    if not config.DATA_DIR.exists():
        return []
    records = []
    for p in sorted(config.DATA_DIR.glob("*.json")):
        records.append(ResearchRecord.model_validate_json(p.read_text(encoding="utf-8")))
    return records


if __name__ == "__main__":
    # 测试 demo：create -> get -> update_status -> list_all -> 清理（纯本地，不联网）
    rid = "demo-storage-test"
    try:
        created = create(rid, "存储模块自检主题")
        print("create:", created.status, created.topic)

        got = get(rid)
        assert got is not None and got.topic == "存储模块自检主题"
        print("get   :", got.research_id, got.status)

        update_status(rid, "running")
        got2 = get(rid)
        assert got2 is not None and got2.status == Status.running
        print("update_status:", got2.status)

        assert any(r.research_id == rid for r in list_all())
        print("list_all 包含该记录 OK")
        print("存储自检 OK")
    finally:
        # 清理测试记录，避免污染真实数据目录
        p = config.DATA_DIR / f"{rid}.json"
        if p.exists():
            p.unlink()
        print("已清理测试记录:", not p.exists())
