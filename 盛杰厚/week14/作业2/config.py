"""全局配置 —— 多模态 RAG 问答（Kafka + MinerU）

所有可调项集中在这里，优先级：**真实环境变量 > 同目录 .env 文件 > 代码默认值**。

════════════════════════════════════ 数据与目录 ════════════════════════════════════
DATA_DIR            运行期数据根目录，默认 ./data
                    ├── uploads/    原始 PDF
                    ├── parsed/     解析产物（blocks.json / full.md）
                    ├── images/     抽出的图片（问答时按 URL 回给前端）
                    └── index/      FAISS 向量索引 + sqlite 元数据库

════════════════════════════════════ Kafka ═════════════════════════════════════════
KAFKA_BOOTSTRAP_SERVERS   broker 地址，默认 localhost:9092（docker compose 起的 KRaft 单节点）
KAFKA_CLIENT_ID           客户端标识
USE_KAFKA               1=走真实 Kafka（默认）；0=走进程内总线（无 broker 时调试前端用）
ALLOW_LOCAL_FALLBACK    Kafka 连不上时是否自动降级为进程内总线，默认 1
KAFKA_CREATE_TOPICS     启动时自动建 topic，默认 1

════════════════════════════════════ MinerU（PDF 解析）════════════════════════════════
MINERU_BASE_URL         mineru-api 服务地址，例如 http://127.0.0.1:8002
                        （本地启动： pip install -U "mineru[core]" && mineru-api --host 127.0.0.1 --port 8002）
MINERU_API_PATH         解析接口路径，默认 /file_parse
MINERU_BACKEND          pipeline / vlm-engine / hybrid-auto-engine，默认 pipeline
MINERU_PARSE_METHOD     auto / txt / ocr，默认 auto
MINERU_TIMEOUT          单次解析超时（秒），默认 600
MINERU_TOKEN            可选，Authorization: Bearer <token>
MINERU_STRICT           1=MinerU 不可用即判「解析失败」；0=自动用内置兜底解析器（默认）

════════════════════════════════════ Embedding ═══════════════════════════════════════
EMBED_BACKEND           openai（默认，百炼 text-embedding-v4）/ local（本地 bge）/ mock
EMBED_OPENAI_MODEL      默认 text-embedding-v4（1024 维）；EMBED_OPENAI_BATCH 默认 10（接口上限）
EMBED_MODEL             仅 local 后端使用，默认 BAAI/bge-small-zh-v1.5（512 维，中文检索）
EMBED_MODEL_DIR         本地模型目录；留空则自动从 HF / ModelScope 缓存解析
EMBED_DEVICE            cpu / mps / cuda，默认 auto
EMBED_QUERY_INSTRUCTION bge 中文检索的 query 前缀（doc 侧不加）
HF_HOME                 想换缓存位置时设置；默认沿用 ~/.cache/huggingface

════════════════════════════════════ 大模型（阿里百炼 / DashScope）════════════════════
LLM_BACKEND             dashscope（默认）/ mock
DASHSCOPE_API_KEY       百炼 API Key（必填，缺失时自动按 mock 跑通链路并给出提示）
LLM_BASE_URL            OpenAI 兼容地址，默认 https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL               多模态模型（图文一起送入），默认 qwen3-vl-plus（部分账号只有 qwen-vl-max，按实际开通的填）
LLM_TEXT_MODEL          纯文本问答模型，默认 qwen-plus（没有图时用它，省 token）
LLM_MAX_TOKENS / LLM_TEMPERATURE / LLM_TIMEOUT

════════════════════════════════════ 检索 ════════════════════════════════════════════
RETRIEVAL_TOP_K         向量召回条数，默认 8
RETRIEVAL_MIN_SCORE     相似度下限（余弦），默认 0.30
RETRIEVAL_MAX_IMAGES    单轮最多回给模型的图片数，默认 4
RETRIEVAL_CHUNK_SIZE    切块长度（字符），默认 600
RETRIEVAL_CHUNK_OVERLAP 块间重叠，默认 80
QA_HISTORY_TURNS        多轮上下文轮数，默认 3

════════════════════════════════════ 其他 ════════════════════════════════════════════
MAX_UPLOAD_MB           单文件上传上限，默认 80
WORKERS_INPROC          1=Web 进程内自带消费线程（默认，一条命令跑全部）；0=由 worker.py 独立进程消费
PORT / HOST             默认 0.0.0.0:8010
MAX_UPLOAD_MB           单个 PDF 上限，默认 80
AUTO_SAVE_INDEX         1=每次写入向量后立刻落盘 FAISS（默认），0=只在退出时落盘
EMBED_THREADS           torch/OpenMP 线程数上限，默认 2（机器繁忙时可降到 1）

注意：本文件顶部在导入 torch / faiss 之前设置了 KMP_DUPLICATE_LIB_OK=TRUE，
以及 OMP_NUM_THREADS / MKL_NUM_THREADS —— macOS 上 torch 与 faiss 各带一份 libomp，
同进程加载第二份会直接 abort，详见 README「踩坑记录」第 6 条。
"""
from __future__ import annotations

import os
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# 必须在 import torch / faiss 之前设置的进程级环境变量
#   * macOS 上 torch 和 faiss-cpu 各自静态链接了一份 libomp，先加载谁都会让另一份报
#     "OMP: Error #15 ... libomp.dylib already initialized" 并**直接 abort 掉进程**
#     （症状：uvicorn 起来后无声无息消失、客户端一直连不上）。KMP_DUPLICATE_LIB_OK
#     是官方给出的、唯一的绕过开关。
#   * 线程数默认压到 2：本地 embedding + 多个 Kafka 消费线程同进程时，torch/OpenMP
#     默认按核数开线程会把机器压满（实测 load average 上百，所有请求排队超时）。
# ══════════════════════════════════════════════════════════════════════════════
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_THREADS = os.environ.get("EMBED_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", _THREADS)
os.environ.setdefault("MKL_NUM_THREADS", _THREADS)

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 极简 .env 解析（不引入 python-dotenv，风格与 week13/app.py 一致）
# ---------------------------------------------------------------------------
def load_dotenv(path: Path) -> None:
    """把 .env 读进 os.environ；**已存在的真实环境变量优先**，不会被覆盖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # 支持行尾注释：`KEY=value   # 说明`。值里不会出现 " #"（API key / 模型名都不会），
        # 但不处理的话注释会被当成值的一部分（模型名后面拖一串注释，调用必然失败）。
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        value = value.strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")


def _s(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _i(name: str, default: int) -> int:
    try:
        return int(_s(name) or default)
    except ValueError:
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(_s(name) or default)
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    v = _s(name)
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
DATA_DIR = Path(_s("DATA_DIR") or (BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
PARSED_DIR = DATA_DIR / "parsed"
IMAGE_DIR = DATA_DIR / "images"
INDEX_DIR = DATA_DIR / "index"
DB_PATH = INDEX_DIR / "meta.sqlite3"
FAISS_PATH = INDEX_DIR / "chunks.faiss"
FAISS_META_PATH = INDEX_DIR / "chunks.meta.json"
LOG_DIR = DATA_DIR / "logs"

for _d in (DATA_DIR, UPLOAD_DIR, PARSED_DIR, IMAGE_DIR, INDEX_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = _s("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_CLIENT_ID = _s("KAFKA_CLIENT_ID", "mmrag-week14")
USE_KAFKA = _b("USE_KAFKA", True)
ALLOW_LOCAL_FALLBACK = _b("ALLOW_LOCAL_FALLBACK", True)
KAFKA_CREATE_TOPICS = _b("KAFKA_CREATE_TOPICS", True)
KAFKA_PARTITIONS = _i("KAFKA_PARTITIONS", 3)
KAFKA_REPLICATION = _i("KAFKA_REPLICATION_FACTOR", 1)

# ---- topic 约定（消息契约见 messages.py）----
TOPIC_UPLOAD = _s("TOPIC_UPLOAD", "mmrag.pdf.upload")     # Web -> 解析 Worker：待解析任务
TOPIC_PARSED = _s("TOPIC_PARSED", "mmrag.pdf.parsed")     # 解析 Worker -> 入库 Worker：解析产物
TOPIC_STATUS = _s("TOPIC_STATUS", "mmrag.doc.status")     # 各 Worker -> Web：解析状态（待解析/解析中/解析完成）
TOPIC_QA_REQ = _s("TOPIC_QA_REQ", "mmrag.qa.request")     # Web -> QA Worker：多模态提问
TOPIC_QA_ANS = _s("TOPIC_QA_ANS", "mmrag.qa.answer")      # QA Worker -> Web：答案增量/引用/结束

GROUP_PARSE = _s("GROUP_PARSE", "mmrag-parser")
GROUP_INDEX = _s("GROUP_INDEX", "mmrag-indexer")
GROUP_QA = _s("GROUP_QA", "mmrag-qa")


# ---------------------------------------------------------------------------
# MinerU
# ---------------------------------------------------------------------------
MINERU_BASE_URL = _s("MINERU_BASE_URL", "http://127.0.0.1:8002").rstrip("/")
MINERU_API_PATH = _s("MINERU_API_PATH", "/file_parse")
MINERU_BACKEND = _s("MINERU_BACKEND", "pipeline")
MINERU_PARSE_METHOD = _s("MINERU_PARSE_METHOD", "auto")
MINERU_TIMEOUT = _i("MINERU_TIMEOUT", 600)
MINERU_TOKEN = _s("MINERU_TOKEN")
MINERU_STRICT = _b("MINERU_STRICT", False)
MINERU_MAX_PAGES = _i("MINERU_MAX_PAGES", 0)  # 0 = 不限制
MINERU_MODE = _s("MINERU_MODE", "auto").lower()   # auto | sync | async（/tasks 支持进度）
MINERU_POLL_INTERVAL = _f("MINERU_POLL_INTERVAL", 1.5)

# ---- 内置兜底解析器（parsers/local_pdf.py）参数 ----
PREFERRED_PARSER = _s("PREFERRED_PARSER", "").lower()   # 留空=auto(mineru 优先)；mineru / pdf_local
LOCAL_FIGURE_DPI = _i("LOCAL_FIGURE_DPI", 150)          # 图片区域渲染分辨率
LOCAL_MIN_FIGURE_EDGE = _f("LOCAL_MIN_FIGURE_EDGE", 55)   # 小于此边长(pt)的图当装饰
LOCAL_MIN_FIGURE_AREA = _f("LOCAL_MIN_FIGURE_AREA", 3000)
LOCAL_MAX_FIGURES = _i("LOCAL_MAX_FIGURES", 200)
LOCAL_TABLES = _b("LOCAL_TABLES", True)                 # 是否识别表格
LOCAL_MAX_PAGES = _i("LOCAL_MAX_PAGES", 0)              # 0 = 全部页


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
# 默认走百炼的远程向量模型 text-embedding-v4：
#   - 不加载 torch（省 ~0.5GB 内存、启动快），一台机器上跑 Web + 多个 Worker 更稳；
#   - 代价是每个块都要出网请求（已按 EMBED_OPENAI_BATCH 分批）。
# 想换回完全离线的本地 bge：EMBED_BACKEND=local
EMBED_BACKEND = _s("EMBED_BACKEND", "openai").lower()     # openai | local | mock
EMBED_MODEL = _s("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")  # 仅 local 后端使用
EMBED_MODEL_DIR = _s("EMBED_MODEL_DIR")
EMBED_DEVICE = _s("EMBED_DEVICE", "auto")
EMBED_BATCH_SIZE = _i("EMBED_BATCH_SIZE", 32)
EMBED_MAX_LENGTH = _i("EMBED_MAX_LENGTH", 512)
# bge 中文检索模型：query 侧加指令，doc 侧不加（官方推荐）
EMBED_QUERY_INSTRUCTION = _s(
    "EMBED_QUERY_INSTRUCTION", "为这个句子生成表示以用于检索相关文章："
)
# 远程 embedding（EMBED_BACKEND=openai 时生效）
EMBED_OPENAI_BASE_URL = _s("EMBED_OPENAI_BASE_URL", _s("LLM_BASE_URL"))
EMBED_OPENAI_API_KEY = _s("EMBED_OPENAI_API_KEY", _s("DASHSCOPE_API_KEY"))
EMBED_OPENAI_MODEL = _s("EMBED_OPENAI_MODEL", "text-embedding-v4")   # 1024 维
# 远程 embedding 接口的批量上限（DashScope text-embedding-* 限制单次 <= 10）
EMBED_OPENAI_BATCH = _i("EMBED_OPENAI_BATCH", 10)
# 兜底解析器抽图后，是否调用多模态大模型给图片生成描述（描述参与向量检索 = 图片可被检索到）
IMAGE_CAPTION = _b("IMAGE_CAPTION", True)


# ---------------------------------------------------------------------------
# 大模型：阿里百炼（OpenAI 兼容模式）
# ---------------------------------------------------------------------------
DASHSCOPE_API_KEY = _s("DASHSCOPE_API_KEY") or _s("LLM_API_KEY")
LLM_BACKEND = _s("LLM_BACKEND", "dashscope").lower()      # dashscope | mock
LLM_BASE_URL = _s("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = _s("LLM_MODEL", "qwen3-vl-plus")               # 多模态：图 + 问 一起送
LLM_TEXT_MODEL = _s("LLM_TEXT_MODEL", "qwen-plus")        # 纯文本
LLM_MAX_TOKENS = _i("LLM_MAX_TOKENS", 1600)
LLM_TEMPERATURE = _f("LLM_TEMPERATURE", 0.2)
LLM_TIMEOUT = _i("LLM_TIMEOUT", 120)
LLM_IMAGE_MIN_EDGE = _i("LLM_IMAGE_MIN_EDGE", 28)         # 百炼要求宽高均 > 28px


# mock 判定：没 key 或显式要求 -> 走 mock（保证无 key 也能把链路跑通）
LLM_MOCK = LLM_BACKEND == "mock" or (LLM_BACKEND == "dashscope" and not DASHSCOPE_API_KEY)


# ---------------------------------------------------------------------------
# 检索 / 问答
# ---------------------------------------------------------------------------
RETRIEVAL_TOP_K = _i("RETRIEVAL_TOP_K", 8)
RETRIEVAL_MIN_SCORE = _f("RETRIEVAL_MIN_SCORE", 0.30)
RETRIEVAL_MAX_IMAGES = _i("RETRIEVAL_MAX_IMAGES", 4)
CHUNK_SIZE = _i("CHUNK_SIZE", 600)
CHUNK_OVERLAP = _i("CHUNK_OVERLAP", 80)
QA_HISTORY_TURNS = _i("QA_HISTORY_TURNS", 3)


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------
MAX_UPLOAD_MB = _i("MAX_UPLOAD_MB", 80)
# 每次写入向量后立刻把 FAISS 索引落盘（演示项目更关心「崩了不丢」，生产可关掉）
AUTO_SAVE_INDEX = _b("AUTO_SAVE_INDEX", True)
# 启动时预热 embedding（同步加载，宁可启动慢也不让第一个请求卡在模型导入上）
EMBED_WARM_ON_START = _b("EMBED_WARM_ON_START", True)
ALLOWED_SUFFIXES = {".pdf"}
WORKERS_INPROC = _b("WORKERS_INPROC", True)
HOST = _s("HOST", "0.0.0.0")
PORT = _i("PORT", 8010)


def summary() -> dict:
    """给 /api/health 和启动横幅用：一眼看清当前真正生效的配置。"""
    return {
        "kafka": {
            "enabled": USE_KAFKA,
            "servers": KAFKA_BOOTSTRAP_SERVERS,
            "topics": [TOPIC_UPLOAD, TOPIC_PARSED, TOPIC_STATUS, TOPIC_QA_REQ, TOPIC_QA_ANS],
            "fallback_allowed": ALLOW_LOCAL_FALLBACK,
        },
        "parser": {
            "mineru_url": MINERU_BASE_URL + MINERU_API_PATH,
            "mineru_backend": MINERU_BACKEND,
            "strict": MINERU_STRICT,
            "fallback": "内置 pdfplumber/pypdfium2 解析器",
        },
        "embedding": {
            "backend": EMBED_BACKEND,
            # 按实际后端报模型名（以前这里固定报 bge，切到远程后会误导排查）
            "model": (EMBED_OPENAI_MODEL if EMBED_BACKEND == "openai"
                      else "mock-hash-ngram" if EMBED_BACKEND == "mock" else EMBED_MODEL),
            "mock": EMBED_BACKEND == "mock",
        },
        "llm": {
            "backend": "mock" if LLM_MOCK else "dashscope",
            "vision_model": LLM_MODEL,
            "text_model": LLM_TEXT_MODEL,
            "has_key": bool(DASHSCOPE_API_KEY),
            "base_url": LLM_BASE_URL,
        },
        "paths": {
            "data": str(DATA_DIR),
            "db": str(DB_PATH),
            "faiss": str(FAISS_PATH),
        },
    }


if __name__ == "__main__":  # python config.py 直接打印当前配置
    import json

    print(json.dumps(summary(), ensure_ascii=False, indent=2))
