"""Upload validation and normalisation.

Uploaded files are never stored under their original name: they are saved
(encrypted) under a random id, and only a sanitised display name is kept.
Images are re-encoded, which strips EXIF/GPS metadata and caps their size.
"""
from __future__ import annotations

import base64
import io
import re
import uuid

from . import config

IMAGE_TYPES = ["png", "jpg", "jpeg", "webp", "gif"]
VIDEO_TYPES = ["mp4", "webm", "mov", "m4v"]


class MediaError(ValueError):
    pass


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "file").split("/")[-1].split("\\")[-1])
    return name.strip("._")[:60] or "file"


def process_image(data: bytes, max_px: int = 1024) -> tuple[bytes, str]:
    from PIL import Image, ImageOps

    if len(data) > config.MAX_IMAGE_MB * 1024 * 1024:
        raise MediaError(f"Images must be under {config.MAX_IMAGE_MB} MB.")
    try:
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_px, max_px))
        out = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img.convert("RGBA").save(out, "PNG", optimize=True)
            return out.getvalue(), "image/png"
        img.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue(), "image/jpeg"
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("That file is not a valid image.") from exc


def check_video(data: bytes, filename: str) -> str:
    limit = config.max_video_bytes()
    if len(data) > limit:
        raise MediaError(f"Videos must be under {limit // (1024 * 1024)} MB. "
                         "Compress it, or paste a video link instead.")
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "video/mp4"          # mp4 / m4v / mov containers
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    raise MediaError(f"'{safe_name(filename)}' is not a valid MP4 or WebM video.")


def check_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https://[^\s<>\"']{4,500}$", url):
        raise MediaError("Links must start with https:// and contain no spaces.")
    return url


def new_media_id() -> str:
    return uuid.uuid4().hex


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"
