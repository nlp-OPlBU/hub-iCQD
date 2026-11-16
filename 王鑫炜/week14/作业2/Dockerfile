FROM python:3.12-slim
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
COPY scripts ./scripts
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data MINERU_DEVICE=cpu
EXPOSE 8014
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8014"]
