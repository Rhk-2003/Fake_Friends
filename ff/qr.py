"""QR code for the player link."""
from __future__ import annotations

import io


def qr_png(url: str, scale: int = 8) -> bytes:
    import segno

    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=scale, border=2,
                                    dark="#14101f", light="#ffffff")
    return buf.getvalue()
