ARG NINO_RUNTIME_BASE_IMAGE=python:3.12-slim
FROM ${NINO_RUNTIME_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NINO_RUNTIME_MODE=demo \
    NINO_SKILLS_PATH=/app/shared/skills

WORKDIR /app/runtime

# COPY merges directories from the build context. Clear source left by an offline base image first,
# otherwise files deleted by the current architecture can leak into the new wheel.
RUN rm -rf /app/runtime/src

COPY agent/python/pyproject.toml ./
COPY agent/python/src ./src
RUN rm -rf ./src/nino_agent_runtime ./src/nino_agent_runtime.egg-info
ARG NINO_PIP_EXTRAS=
# Offline rebuilds may use an older Runtime image as the base. Remove its package directory so
# files deleted from the current wheel cannot survive as importable orphan modules.
RUN rm -rf /usr/local/lib/python3.12/site-packages/nino_agent_runtime \
        /usr/local/lib/python3.12/site-packages/nino_agent_runtime-*.dist-info \
        /usr/local/lib/python3.12/site-packages/api \
        /usr/local/lib/python3.12/site-packages/framework \
        /usr/local/lib/python3.12/site-packages/harness \
        /usr/local/lib/python3.12/site-packages/infrastructure \
        /usr/local/lib/python3.12/site-packages/runtime \
        /usr/local/lib/python3.12/site-packages/bootstrap.py \
        /usr/local/lib/python3.12/site-packages/demo.py \
        /usr/local/lib/python3.12/site-packages/version.py \
    && pip install --no-cache-dir ".${NINO_PIP_EXTRAS}" \
    && rm -rf /usr/local/lib/python3.12/site-packages/nino_agent_runtime

RUN rm -rf /app/shared
COPY agent/shared /app/shared

EXPOSE 8090

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8090"]
