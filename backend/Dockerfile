# syntax=docker/dockerfile:1.7
# WardPay API image. Targets:
#   runtime (default)  API / Celery worker / migrator, non-root, no dev tools
#   test               runs the test suite against TEST_DATABASE_URL
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
WORKDIR /code
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/code/.venv UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
# --locked fails if uv.lock is out of date with pyproject.toml
RUN uv sync --locked --no-install-project --no-dev

FROM build AS test
RUN uv sync --locked --no-install-project
COPY app ./app
COPY alembic ./alembic
COPY tests ./tests
COPY alembic.ini pyproject.toml ./
ENV PATH="/code/.venv/bin:$PATH"
CMD ["python", "-m", "pytest", "-q"]

FROM ${PYTHON_IMAGE} AS runtime
WORKDIR /code
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/code/.venv/bin:$PATH"
RUN groupadd --gid 10001 wardpay && useradd --uid 10001 --gid wardpay --no-create-home wardpay
COPY --from=build /code/.venv /code/.venv
COPY --chown=10001:10001 app /code/app
COPY --chown=10001:10001 alembic /code/alembic
COPY --chown=10001:10001 alembic.ini /code/
COPY --chown=10001:10001 bin /code/bin
USER 10001:10001
EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/api/v1/health', timeout=3)"
# Proxy headers are only honoured from the address set in FORWARDED_ALLOW_IPS (the reverse proxy)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8002 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
