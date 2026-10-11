FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data/hf-cache

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py engine.py ./
COPY static ./static

ENV PUBLIC_DEPLOY=1
ENV DATA_DIR=/app/data
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn app:APP --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
