FROM python:3.12.11-slim-bookworm

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1

COPY requirements.txt /tmp/agentrace-verification-requirements.txt
RUN python -m pip install --no-cache-dir \
        -r /tmp/agentrace-verification-requirements.txt \
    && python -m pip check \
    && rm /tmp/agentrace-verification-requirements.txt \
    && groupadd --gid 65532 verifier \
    && useradd --uid 65532 --gid 65532 --no-create-home --home-dir /tmp verifier

WORKDIR /workspace
USER 65532:65532

LABEL org.opencontainers.image.title="AgentTrace deterministic verifier" \
      org.opencontainers.image.description="Phase 6 isolated Python verification tools"
