#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
统一的配置读取模块（三个示例共用，约 100 行）。

设计目标：
  1. 所有连接信息都来自 config.yaml，代码里不出现 host / port / topic 等常量；
  2. 支持用环境变量覆盖，便于在容器、CI 或临时改端口时不动配置文件；
  3. 配置缺失或格式错误时，给出能直接照做的中文提示。

优先级：环境变量 > config.yaml > 代码里的默认值（仅用于非关键项）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = BASE_DIR / "config.yaml"
EXAMPLE_CONFIG_FILE = BASE_DIR / "config.example.yaml"

# 环境变量名 -> (配置段, 配置项, 类型转换函数)
# 例如 REDIS_PORT=6380 会覆盖 redis.port；KAFKA_TOPIC=xxx 会覆盖 kafka.topic
_ENV_OVERRIDES: dict[str, tuple[str, str, type]] = {
    "REDIS_HOST": ("redis", "host", str),
    "REDIS_PORT": ("redis", "port", int),
    "REDIS_DB": ("redis", "db", int),
    "REDIS_PASSWORD": ("redis", "password", str),
    "REDIS_KEY_PREFIX": ("redis", "key_prefix", str),
    "REDIS_MAX_MESSAGES": ("redis", "max_messages", int),
    "REDIS_TTL_SECONDS": ("redis", "ttl_seconds", int),
    "KAFKA_BOOTSTRAP_SERVERS": ("kafka", "bootstrap_servers", str),
    "KAFKA_TOPIC": ("kafka", "topic", str),
    "KAFKA_GROUP_ID": ("kafka", "group_id", str),
    "KAFKA_AUTO_OFFSET_RESET": ("kafka", "auto_offset_reset", str),
    "KAFKA_POLL_TIMEOUT_MS": ("kafka", "poll_timeout_ms", int),
}


def _to_bool(raw: str) -> bool:
    """把 "true"/"false" 之类的字符串转成 bool。"""
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides(config: dict[str, Any]) -> None:
    """用环境变量覆盖配置文件中的值（原地修改）。"""
    for env_name, (section, key, caster) in _ENV_OVERRIDES.items():
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            continue
        try:
            config.setdefault(section, {})[key] = caster(raw)
        except ValueError as exc:
            raise SystemExit(f"环境变量 {env_name}={raw!r} 格式不正确：{exc}") from exc

    # 布尔项单独处理，避免 bool("false") == True 这种经典坑
    if os.getenv("KAFKA_ENABLE_AUTO_COMMIT"):
        config.setdefault("kafka", {})["enable_auto_commit"] = _to_bool(
            os.environ["KAFKA_ENABLE_AUTO_COMMIT"]
        )


def kafka_api_version(kafka_config: dict[str, Any]) -> tuple[int, ...] | None:
    """
    把配置里的 "3.8" 解析成 kafka-python 需要的 (3, 8)。

    显式指定版本可以跳过客户端启动时的 ApiVersions 探测；
    部分网络环境（例如经 WSL 端口转发连 Docker 里的 Kafka）探测会超时，
    表现为 NoBrokersAvailable，此时固定版本最省事。留空则仍由客户端自动探测。
    """
    raw = kafka_config.get("api_version")
    if raw in (None, ""):
        return None
    try:
        # 只取主版本和次版本，忽略补丁号（kafka-python 不支持 (3, 8, 0) 这种三元组）
        parts = tuple(int(part) for part in str(raw).strip().split("."))[:2]
    except ValueError as exc:
        raise SystemExit(f"kafka.api_version={raw!r} 格式不正确，应形如 '3.8'") from exc
    if len(parts) != 2:
        raise SystemExit(f"kafka.api_version={raw!r} 格式不正确，应形如 '3.8'")
    return parts


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """
    读取并校验配置。

    :param path: 自定义配置文件路径；默认使用脚本同目录的 config.yaml
    :return: 形如 {"redis": {...}, "kafka": {...}} 的字典
    """
    config_file = Path(path) if path else DEFAULT_CONFIG_FILE

    if not config_file.exists():
        hint = (
            f"找不到配置文件：{config_file}\n"
            f"请先复制示例配置后再运行：\n"
            f"    copy \"{EXAMPLE_CONFIG_FILE.name}\" \"{config_file.name}\"   (Windows)\n"
            f"    cp   {EXAMPLE_CONFIG_FILE.name} {config_file.name}          (macOS/Linux)"
        )
        raise SystemExit(hint)

    try:
        with config_file.open("r", encoding="utf-8") as fp:
            config = yaml.safe_load(fp) or {}
    except yaml.YAMLError as exc:
        raise SystemExit(f"配置文件 {config_file} 不是合法的 YAML：\n{exc}") from exc

    if not isinstance(config, dict):
        raise SystemExit(f"配置文件 {config_file} 的内容必须是键值对（字典）结构")

    _apply_env_overrides(config)

    # 只校验两个示例真正会用到的配置段，避免过度设计
    for section in ("redis", "kafka"):
        if not isinstance(config.get(section), dict):
            raise SystemExit(f"配置文件缺少 `{section}:` 配置段，请参考 {EXAMPLE_CONFIG_FILE.name}")

    return config
