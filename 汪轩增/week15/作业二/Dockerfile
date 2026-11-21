

FROM python:3.12-slim

# 容器只启用 OpenAI Agents 后端；日志直接输出到 docker logs。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKFLOWS=agents \
    OPENAI_BASE_URL=https://api.deepseek.com \
    OPENAI_MODEL=deepseek-flash

WORKDIR /app

COPY ["作业一/requirements-agents.txt", "requirements-agents.txt"]
RUN python -m pip install --no-cache-dir -r requirements-agents.txt \
    && useradd --system --uid 10001 --create-home appuser


COPY ["作业一/app/", "app/"]
COPY ["作业一/web/", "web/"]

# 用非 root 用户运行，降低容器被利用的风险。
USER appuser

EXPOSE 8000

# 健康检查只访问本地接口，不调用模型。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/health', timeout=3).close()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
