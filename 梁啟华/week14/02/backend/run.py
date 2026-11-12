"""
启动入口 - 从 backend/ 目录运行:
    python run.py
"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))


def main():
    import uvicorn
    from app.config_loader import Config

    cfg_server = Config.get_section('server')
    host = cfg_server.get('host', '0.0.0.0')
    port = cfg_server.get('port', 8000)

    print()
    print("=" * 60)
    print(" 多模态 RAG 问答系统 启动中...")
    print("=" * 60)
    print(f" 前端页面: http://localhost:{port}/")
    print(f" API 文档: http://localhost:{port}/docs")
    print("=" * 60)
    print()

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=cfg_server.get('reload', True),
    )


if __name__ == "__main__":
    main()
