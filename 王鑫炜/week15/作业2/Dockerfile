FROM python:3.12-slim

# 容器只启用 OpenAI Agents，应用日志直接输出到容器日志。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKFLOWS=agents
WORKDIR /app

# 先安装依赖再复制源码，修改业务代码时可以复用依赖层。
COPY ["作业1/requirements-agents.txt", "requirements.txt"]
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 appuser

COPY ["作业1/app/", "app/"]
COPY ["作业1/web/", "web/"]

USER appuser
EXPOSE 8000
# 健康检查只访问本地接口，不调用模型。
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/health', timeout=2).close()"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
