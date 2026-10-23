FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# Configuration comes from the compose env file at runtime.
CMD ["sh", "-c", "python scripts/migrate.py || echo migrate_failed; exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
