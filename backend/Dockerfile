# syntax=docker/dockerfile:1
# Backend: FastAPI + local embeddings (sentence-transformers, CPU) + ChromaDB client + SQLite.
# Build context: repository root (needs data/ for sample documents and discovery datasets).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf-cache

WORKDIR /app

# Optional build secret `extra_ca`: a CA bundle for TLS-inspecting corporate proxies.
COPY backend/requirements.txt backend/requirements-ml.txt ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.txt

# Local embedding model. CPU-only PyTorch keeps the image small (~1 GB less than the CUDA build).
ARG INSTALL_ML=true
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.15.0
ARG EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca REQUESTS_CA_BUNDLE=/run/secrets/extra_ca; fi; \
    if [ "$INSTALL_ML" = "true" ]; then \
      pip install --index-url "$TORCH_INDEX_URL" "torch==$TORCH_VERSION" && \
      pip install -r requirements-ml.txt && \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('$EMBEDDING_MODEL')" ; \
    fi

# The model is baked into the image, so runtime needs no internet for embeddings.
ARG HF_OFFLINE=1
ENV HF_HUB_OFFLINE=$HF_OFFLINE \
    EMBEDDING_MODEL=$EMBEDDING_MODEL \
    DATA_DIR=/data \
    BUNDLED_DATA_DIR=/opt/bundled-data \
    CHROMA_MODE=http \
    PORT=8000

COPY backend/ /app/
COPY data/sample_documents /opt/bundled-data/sample_documents
COPY data/discovery /opt/bundled-data/discovery

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser /data /app \
    && chmod -R a+rX /opt
USER appuser
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
