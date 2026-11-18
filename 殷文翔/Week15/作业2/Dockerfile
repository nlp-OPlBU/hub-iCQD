FROM python:3.12-slim

RUN python -m pip install --no-cache-dir fastapi uvicorn openai-agents openai pydantic pydantic-settings requests

WORKDIR /app/backend
COPY backend/openai_agents_version/ ./openai_agents_version/
COPY frontend/ /app/frontend/

CMD ["python", "-m", "uvicorn", "openai_agents_version.orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]