# GPU-free stand-in for a vLLM-Omni TTS backend. Speaks the same API subset
# (/health, /v1/models, /v1/audio/speech) and returns a tone whose length
# tracks the input text. For UI/controller development only.
FROM python:3.12-slim
COPY mock_server.py /mock_server.py
ENTRYPOINT ["python", "/mock_server.py"]
