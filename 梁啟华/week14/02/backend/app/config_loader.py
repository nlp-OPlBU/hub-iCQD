"""
配置加载模块

支持从 YAML 文件加载配置，并处理环境变量引用 ${VAR_NAME}
"""
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from loguru import logger


# 项目根目录（config.py 位于 backend/app/，向上回溯到项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 默认配置文件路径
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def _resolve_env_vars(value: Any) -> Any:
    """递归解析配置中的环境变量引用 ${VAR_NAME} 或 ${VAR_NAME:default}"""
    if isinstance(value, str):
        pattern = r'\$\{([^}]+)\}'

        def _replace(match: re.Match) -> str:
            expr = match.group(1)
            if ':' in expr:
                var_name, default = expr.split(':', 1)
                return os.environ.get(var_name.strip(), default.strip())
            return os.environ.get(expr, match.group(0))

        return re.sub(pattern, _replace, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载 YAML 配置文件"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    path = path if path.is_absolute() else PROJECT_ROOT / path

    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    logger.info(f"加载配置文件: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)

    config = _resolve_env_vars(raw_config)
    config = _normalize_paths(config)
    return config


def _normalize_paths(config: Dict[str, Any]) -> Dict[str, Any]:
    """将相对路径转换为绝对路径"""
    if 'storage' in config:
        storage = config['storage']
        for key in ['base_dir', 'upload_dir', 'parsed_dir', 'image_dir', 'db_path']:
            if key in storage:
                p = Path(storage[key])
                if not p.is_absolute():
                    storage[key] = str(PROJECT_ROOT / p)
        for dir_key in ['base_dir', 'upload_dir', 'parsed_dir', 'image_dir']:
            if dir_key in storage:
                Path(storage[dir_key]).mkdir(parents=True, exist_ok=True)

    if 'vector_store' in config and 'persist_dir' in config['vector_store']:
        p = Path(config['vector_store']['persist_dir'])
        if not p.is_absolute():
            config['vector_store']['persist_dir'] = str(PROJECT_ROOT / p)
        Path(config['vector_store']['persist_dir']).mkdir(parents=True, exist_ok=True)

    return config


class Config:
    """配置单例"""
    _instance: Optional[Dict[str, Any]] = None

    @classmethod
    def get(cls) -> Dict[str, Any]:
        if cls._instance is None:
            cls._instance = load_config()
        return cls._instance

    @classmethod
    def reload(cls) -> Dict[str, Any]:
        cls._instance = load_config()
        return cls._instance

    @classmethod
    def get_section(cls, section: str) -> Dict[str, Any]:
        return cls.get().get(section, {})
