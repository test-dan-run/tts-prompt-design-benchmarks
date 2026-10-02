"""Tiny WAV inspection helpers (no numpy/soundfile dependency)."""
from __future__ import annotations

import struct


def wav_info(data: bytes) -> dict | None:
    """Return sample rate, channels, bit depth and duration of a RIFF/WAVE blob.

    Handles float32 WAVs and streamed WAVs whose size fields are 0/0xFFFFFFFF.
    Returns None when the payload is not a parseable WAV.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        chunk_id, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = pos + 8
        if chunk_id == b"fmt ":
            _fmt_tag, channels, rate, byte_rate, _align, bits = struct.unpack("<HHIIHH", data[body : body + 16])
            fmt = {"sample_rate": rate, "channels": channels, "bits_per_sample": bits, "byte_rate": byte_rate}
        elif chunk_id == b"data" and fmt:
            available = len(data) - body
            if size in (0, 0xFFFFFFFF) or size > available:
                size = available
            fmt["duration_s"] = size / fmt["byte_rate"] if fmt["byte_rate"] else 0.0
            return fmt
        pos = body + size + (size & 1)
    return None
