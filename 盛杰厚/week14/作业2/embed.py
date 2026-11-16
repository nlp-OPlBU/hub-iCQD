"""向量化 —— 默认用 **BAAI/bge-small-zh-v1.5** 在本地推理（512 维，中文检索效果好、够快）。

三种后端（EMBED_BACKEND）
------------------------
* ``openai``（**默认**）：调百炼的 ``text-embedding-v4``（1024 维，OpenAI 兼容 /embeddings）。
  不加载 torch，服务内存占用小、启动快；每批最多 10 条（已自动分批）。
* ``local``：sentence-transformers 本地跑 bge-small-zh-v1.5（完全离线，但要 torch ~0.5GB）。
  - 优先读本地缓存（~/.cache/huggingface 或 ~/.cache/modelscope），**没有 key、不联网也能用**；
  - 缓存没命中才去下载（约 95MB）；
  - bge 中文检索的规矩：**query 侧加指令前缀，doc 侧不加**（前缀见 EMBED_QUERY_INSTRUCTION）。
* ``mock``：字符 n-gram 哈希向量，纯 numpy、零依赖，用于「没有模型也要把链路跑通」的自测。
  mock 的相似度只有词面重合度，**检索质量不可信**，/api/health 会如实标出来。

所有向量都做过 L2 归一化，所以 FAISS 用 ``IndexFlatIP`` 内积 == 余弦相似度，
分数可以直接当阈值用（config.RETRIEVAL_MIN_SCORE）。
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

import config

_LOCK = threading.RLock()


class EmbedError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 模型定位：本地缓存 -> 在线下载
# ---------------------------------------------------------------------------
def resolve_model_dir() -> str:
    """返回可直接喂给 SentenceTransformer 的目录/仓库名，并说明来源。"""
    if config.EMBED_MODEL_DIR and Path(config.EMBED_MODEL_DIR).exists():
        return config.EMBED_MODEL_DIR

    repo = config.EMBED_MODEL
    errors: List[str] = []

    # 1) HuggingFace 本地缓存
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(repo_id=repo, local_files_only=True)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hf-cache: {type(exc).__name__}: {exc}")

    # 2) ModelScope（国内网络更顺，很多课程机器上装过）
    try:
        from modelscope import snapshot_download as ms_download

        for cand in (repo, f"AI-ModelScope/{Path(repo).name}"):
            try:
                return ms_download(cand)
            except Exception:  # noqa: BLE001
                continue
        errors.append("modelscope: 未命中缓存且下载失败")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"modelscope-import: {exc}")

    # 3) 在线下载 HF
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(repo_id=repo, local_files_only=False)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hf-online: {type(exc).__name__}: {exc}")

    raise EmbedError(
        f"找不到 embedding 模型 {repo}。可尝试：\n"
        f"  1) 联网自动下载（约 95MB）：pip install -U huggingface_hub 后重试\n"
        f"     国内网络可先 export HF_ENDPOINT=https://hf-mirror.com\n"
        f"  2) 手动指定本地目录：EMBED_MODEL_DIR=/path/to/bge-small-zh-v1.5\n"
        f"  3) 用云端网关：EMBED_BACKEND=openai + EMBED_OPENAI_* \n"
        f"  4) 只验证链路：EMBED_BACKEND=mock\n"
        "细节: " + " | ".join(errors[-4:])
    )


def pick_device(pref: str = "") -> str:
    pref = (pref or config.EMBED_DEVICE).lower()
    if pref in ("cpu", "cuda", "mps"):
        return pref
    if pref not in ("", "auto"):
        return pref
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    # CPU 足够：bge-small 只有 24M 参数，单条 query 毫秒级；不默认上 MPS 以免首次编译拖慢
    return "cpu"


def _cap_threads() -> None:
    """限制底层数学库的线程数。

    torch 默认按核数开 intra-op 线程，FAISS 也会开 OpenMP 线程；
    在「一个进程里还有 5 个 Kafka 消费线程」的场景下，多个进程一起跑会把机器压到
    load average 上百（实测过），请求全部排队超时。演示项目里并发很低，1~2 线程足够。
    """
    n = int(os.environ.get("EMBED_THREADS", "2") or 2)
    os.environ.setdefault("OMP_NUM_THREADS", str(n))
    os.environ.setdefault("MKL_NUM_THREADS", str(n))
    try:
        import torch

        torch.set_num_threads(n)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass  # 已经用过 interop 线程时会抛，忽略
    except Exception:  # noqa: BLE001
        pass
    try:
        import faiss

        faiss.omp_set_num_threads(n)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
class Embedder:
    """线程安全的 embedding 入口（Web 线程与 Worker 线程共用一个实例）。"""

    def __init__(self, backend: str = "", model: str = ""):
        self.backend = (backend or config.EMBED_BACKEND).lower()
        self.model_name = model or config.EMBED_MODEL
        self.device = pick_device()
        self._model = None
        self._dim: Optional[int] = None
        self.error = ""
        self.source = ""
        self.calls = 0
        self.vectors = 0

    # ------------------------------------------------------------ 属性
    @property
    def dim(self) -> int:
        if self._dim:
            return self._dim
        if self.backend == "mock":
            return _MOCK_DIM
        try:
            self._load()
        except Exception:  # noqa: BLE001
            return 0
        return self._dim or 0

    def is_mock(self) -> bool:
        return self.backend == "mock"

    def info(self) -> Dict[str, Any]:
        """**只报告缓存状态，绝不触发模型加载**。

        以前这里读 ``self.dim``，而 ``dim`` 会惰性调 ``_load()`` ——
        于是 ``/api/health`` 一次探活就把 sentence-transformers（连带 transformers）
        的导入与模型加载全压在事件循环上，几十秒内所有请求都卡住。
        健康检查必须是只读、廉价的。
        """
        dim = self._dim or (_MOCK_DIM if self.backend == "mock" else 0)
        if self.backend == "mock":
            model_name = "mock-hash-ngram"
        elif self.backend == "openai":
            model_name = config.EMBED_OPENAI_MODEL      # 远程网关的真实模型名
        else:
            model_name = self.model_name                # 本地 bge 等
        return {
            "backend": self.backend,
            "model": model_name,
            "dim": dim,
            "device": self.device if self.backend == "local" else "",
            "source": self.source,
            "ready": self._model is not None or self.backend in ("mock", "openai"),
            "mock": self.is_mock(),
            "error": self.error,
            "vectors_embedded": self.vectors,
        }

    # ------------------------------------------------------------ 加载
    def _load(self):
        if self._model is not None:
            return self._model
        with _LOCK:
            if self._model is not None:
                return self._model
            _cap_threads()      # 先限制线程数，再导入 torch / 加载模型
            from sentence_transformers import SentenceTransformer

            path = resolve_model_dir()
            self.source = "cache" if str(Path(str(path)).parent).startswith(str(Path.home())) else "download"
            model = SentenceTransformer(path, device=self.device, trust_remote_code=False)
            try:
                max_seq = int(model.max_seq_length or 0)
                if config.EMBED_MAX_LENGTH and max_seq > config.EMBED_MAX_LENGTH:
                    model.max_seq_length = config.EMBED_MAX_LENGTH
            except Exception:  # noqa: BLE001
                pass
            self._model = model
            # sentence-transformers 6 把 get_sentence_embedding_dimension 改名了，两边都兼容
            try:
                self._dim = int(model.get_embedding_dimension())
            except AttributeError:
                self._dim = int(model.get_sentence_embedding_dimension())
            print(f"[embed] 已加载 {self.model_name} dim={self._dim} device={self.device} "
                  f"from={path}", flush=True)
            return model

    def warm(self) -> Dict[str, Any]:
        """预热（避免首个请求把模型加载时间算进响应里）。"""
        if self.backend == "mock":
            self.embed_docs(["预热"])
            return self.info()
        try:
            self.embed_docs(["预热"])
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            # local 挂了不要整个服务起不来：降到 mock，但要如实报告
            print(f"[embed] local 后端失败，降级 mock：{self.error}", flush=True)
            self.backend = "mock"
        return self.info()

    # ------------------------------------------------------------ 编码
    def _encode_raw(self, texts: List[str]) -> np.ndarray:
        model = self._load()
        # sentence-transformers 6.x 的 encode() 不接受多余 kwarg（传 truncate_tokens
        # 会抛 ValueError 并让后端悄悄降级成 mock）；截断长度统一由 max_seq_length 控制。
        vecs = model.encode(
            texts,
            batch_size=config.EMBED_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        arr = np.asarray(vecs, dtype="float32")
        return arr.reshape(len(texts), -1)

    def embed_docs(self, texts: Sequence[str],
                   progress_cb: Optional[Callable[[int, int], None]] = None) -> np.ndarray:
        """文档侧：不加指令前缀。分批编码并可回报进度。"""
        texts = [t if t else " " for t in texts]
        if self.backend == "mock":
            out = np.vstack([_mock_vec(t) for t in texts]) if texts else np.zeros((0, _MOCK_DIM), "float32")
        elif self.backend == "openai":
            out = self._embed_openai(texts, is_query=False)
        else:
            out = np.zeros((len(texts), self.dim), "float32")
            step = max(1, config.EMBED_BATCH_SIZE * 4)
            for i in range(0, len(texts), step):
                batch = texts[i:i + step]
                out[i:i + len(batch)] = self._encode_raw(batch)
                if progress_cb:
                    progress_cb(min(i + len(batch), len(texts)), len(texts))
        self.vectors += len(texts)
        self._dim = out.shape[1] if len(texts) else self._dim
        return out

    def embed_query(self, text: str) -> np.ndarray:
        """查询侧：bge 中文检索需要加指令前缀。"""
        q = (config.EMBED_QUERY_INSTRUCTION + text) if self.backend == "local" else text
        if self.backend == "mock":
            v = _mock_vec(text).reshape(1, -1)
        elif self.backend == "openai":
            v = self._embed_openai([text], is_query=True)
        else:
            v = self._encode_raw([q])
        self.calls += 1
        self.vectors += 1
        if v.shape[1] != (self._dim or v.shape[1]):
            pass
        self._dim = v.shape[1]
        return v[0]

    def _embed_openai(self, texts: List[str], is_query: bool) -> np.ndarray:
        import requests

        base = (config.EMBED_OPENAI_BASE_URL or config.LLM_BASE_URL).rstrip("/")
        key = config.EMBED_OPENAI_API_KEY or config.DASHSCOPE_API_KEY
        if not key:
            raise EmbedError("EMBED_BACKEND=openai 但没配 EMBED_OPENAI_API_KEY / DASHSCOPE_API_KEY")
        # 注意：DashScope 的 text-embedding-* 单次 input 最多 10 条，
        # 超了会直接 400（"batch size is invalid, it should not be larger than 10"），
        # 所以这里必须分批，不能一次性把整篇文档的块发过去。
        prepared = [(config.EMBED_QUERY_INSTRUCTION + t if is_query else t) for t in texts]
        batch = max(1, config.EMBED_OPENAI_BATCH)
        vectors: List[np.ndarray] = []
        for i in range(0, len(prepared), batch):
            chunk = prepared[i:i + batch]
            resp = requests.post(
                f"{base}/embeddings",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": config.EMBED_OPENAI_MODEL, "input": chunk},
                timeout=(15, 60),
            )
            if resp.status_code != 200:
                raise EmbedError(
                    f"embedding 网关 HTTP {resp.status_code}: {resp.text[:240]}"
                    f"（本批 {len(chunk)} 条，EMBED_OPENAI_BATCH={batch}）")
            data = sorted(resp.json().get("data") or [], key=lambda d: d.get("index", 0))
            vectors.extend(np.asarray([d["embedding"] for d in data], dtype="float32"))
        if not vectors:
            return np.zeros((0, 0), dtype="float32")
        arr = np.vstack(vectors)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.clip(norms, 1e-9, None)

    # ------------------------------------------------------------ 兼容旧接口
    def embed(self, texts: Sequence[str], is_query: bool = False) -> np.ndarray:
        return self.embed_query(texts[0]).reshape(1, -1) if is_query and len(texts) == 1 \
            else self.embed_docs(texts)


# ---------------------------------------------------------------------------
# mock 向量：字符 1/2-gram 哈希
# ---------------------------------------------------------------------------
_MOCK_DIM = 256


def _mock_vec(text: str) -> np.ndarray:
    v = np.zeros(_MOCK_DIM, dtype="float32")
    t = (text or "").lower()
    if not t:
        return v
    grams: List[str] = []
    for ch in t:
        if ch.strip():
            grams.append(ch)
    for i in range(len(t) - 1):
        g = t[i:i + 2]
        if g.strip():
            grams.append(g)
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
        v[h % _MOCK_DIM] += 1.0
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_embedder: Optional[Embedder] = None


def get_embedder() -> Embedder:
    """拿到进程内单例。

    **这里刻意不做 ``import sentence_transformers``**：
    transformers 5.x 首次导入要十几到二十秒，若在请求路径（尤其 /api/health）上触发，
    整个 asyncio 事件循环会被同步阻塞，所有接口一起卡死。
    真正的导入与模型加载只在 ``warm()`` / 首次 ``embed_*`` 时发生（启动后台线程预热），
    失败则按已实现的降级逻辑切到 mock。
    """
    global _embedder
    with _LOCK:
        if _embedder is None:
            _embedder = Embedder()
        return _embedder


def reset_embedder() -> None:
    global _embedder
    with _LOCK:
        _embedder = None


if __name__ == "__main__":  # python embed.py -> 自检
    e = get_embedder()
    print("后端:", e.warm())
    docs = [
        "公司2024年度净利润为1.2亿元，同比增长8%。",
        "图 3 给出了系统整体架构：前端通过 Kafka 把上传任务投递给解析 Worker。",
        "本文介绍了一种基于 Transformer 的图像描述生成方法。",
    ]
    vecs = e.embed_docs(docs)
    for q in ["净利润是多少？", "架构是怎么设计的？", "图像描述方法"]:
        v = e.embed_query(q)
        sims = (vecs @ v.reshape(-1, 1)).reshape(-1)
        best = int(np.argmax(sims))
        print(f"Q: {q:<16} -> 命中第{best}条  cos={sims[best]:.3f}  {docs[best][:28]}")
