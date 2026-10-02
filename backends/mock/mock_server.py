import io
import json
import math
import os
import struct
import sys
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.getenv("PORT", "8091"))
NAME = os.getenv("SERVED_MODEL_NAME", "mock")
RATE = 24000


def tone(text: str, prompt: str) -> bytes:
    seconds = min(20.0, 0.5 + 0.06 * len(text))
    freq = 180 + (sum(map(ord, prompt)) % 220)  # different "voice" per prompt
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = bytearray()
        for i in range(int(seconds * RATE)):
            env = min(1.0, i / 2400, (seconds * RATE - i) / 2400)
            frames += struct.pack("<h", int(8000 * env * math.sin(2 * math.pi * freq * i / RATE)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, b"")
        if self.path == "/v1/models":
            return self._send(200, json.dumps({"data": [{"id": NAME, "object": "model"}]}).encode())
        self._send(404, b"{}")

    def do_POST(self):
        if self.path != "/v1/audio/speech":
            return self._send(404, b"{}")
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        print("request:", json.dumps(req, ensure_ascii=False), flush=True)
        self._send(200, tone(req.get("input", ""), req.get("instructions", "")), "audio/wav")


print(f"mock TTS backend '{NAME}' on :{PORT} (args: {sys.argv[1:]})", flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
