# syntax=docker/dockerfile:1

# ---------- 构建阶段：只装依赖，不装源码 ----------
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
# 国内网络构建慢时，用 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --retries 5 --timeout 60 --prefix=/install \
    -i ${PIP_INDEX_URL} -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ORCHESTRATOR=agents

COPY --from=builder /install /usr/local

# 只复制运行需要的文件，client/README 等不进镜像
COPY config.py schemas.py agent_runtime.py sentiment_service.py response_service.py backend.py start.py ./
COPY static ./static

RUN useradd -m -u 1001 appuser \
    && chown -R appuser:appuser /app

USER appuser

# 只暴露后端网关；进程1/2 绑定 127.0.0.1，不对外
EXPOSE 8200

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import json,urllib.request,sys; d=json.load(urllib.request.urlopen('http://127.0.0.1:8200/health', timeout=3)); sys.exit(0 if d.get('status')=='ok' else 1)"

CMD ["python", "/app/start.py"]
