# Thin wrapper around the official vLLM-Omni image.
# No model weights are added here: they are mounted read-only at /models.
ARG VLLM_OMNI_IMAGE=vllm/vllm-omni:v0.30.0
FROM ${VLLM_OMNI_IMAGE}

COPY serve.sh /usr/local/bin/ttsbench-serve
RUN chmod +x /usr/local/bin/ttsbench-serve && mkdir -p /models /opt/ttsbench/deploy
COPY deploy/ /opt/ttsbench/deploy/

ENV PORT=8091 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=5s --start-period=30m --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/local/bin/ttsbench-serve"]
