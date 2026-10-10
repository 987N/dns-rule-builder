FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

RUN python -m venv /opt/uv \
    && /opt/uv/bin/pip install --no-cache-dir uv==0.12.13 \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app

ENV PATH="/opt/uv/bin:${PATH}"
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY config/ ./config/
COPY src/ ./src/
COPY templates/ ./templates/
COPY static/ ./static/
RUN uv sync --frozen --no-dev

USER 10001:10001
CMD ["/app/.venv/bin/uvicorn", "--factory", "dns_rule_builder.web:create_app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
