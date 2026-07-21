FROM python:3.11-slim

ARG APP_GIT_SHA=unknown
ENV APP_GIT_SHA=${APP_GIT_SHA}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY scripts/ scripts/
# /app/data is bind-mounted by Compose, so an image-time copy there would be
# hidden at runtime. Keep an immutable fallback dataset outside that mount.
COPY data/eval/rag_queries.json backend/eval/data/rag_queries.json

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
