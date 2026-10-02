# One-shot helper that fills the shared models volume. Not part of serving.
FROM python:3.12-slim
RUN pip install --no-cache-dir "huggingface_hub[hf_xet]==0.35.3" PyYAML==6.0.2
COPY download.py /usr/local/bin/ttsbench-download
ENV HF_HUB_ENABLE_HF_TRANSFER=0
ENTRYPOINT ["python", "/usr/local/bin/ttsbench-download"]
