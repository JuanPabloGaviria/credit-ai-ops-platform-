ARG PYTHON_BASE_IMAGE=python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d

FROM ${PYTHON_BASE_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN python -m venv /opt/venv
COPY requirements/lock/base.lock /app/requirements/base.lock
RUN pip install --upgrade pip==25.0.1 setuptools==80.9.0 wheel==0.46.2 \
    && pip install --require-hashes -r /app/requirements/base.lock \
    && python -m pip uninstall -y wheel setuptools pip

FROM ${PYTHON_BASE_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    APP_HEALTHCHECK_PATH=/health \
    APP_MODULE=decision_service.main:app \
    PYTHONPATH="/app/packages/shared-kernel/src:/app/packages/contracts/src:/app/packages/observability/src:/app/packages/security/src:/app/services/decision/src"

WORKDIR /app

RUN groupadd --system app && useradd --system --uid 10001 --gid app --create-home app
RUN python -m pip uninstall -y wheel setuptools pip
COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --chown=app:app packages /app/packages
COPY --chown=app:app scripts/docker /app/scripts/docker
COPY --chown=app:app scripts/runtime /app/scripts/runtime
COPY --chown=app:app services/decision /app/services/decision

USER 10001:app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "/app/scripts/docker/healthcheck.py"]
ENTRYPOINT ["python", "/app/scripts/docker/run_service.py"]
