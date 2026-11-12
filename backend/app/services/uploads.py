"""Image uploads (logos, item photos). Stored on local disk for now; move to S3 before scaling."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from ..config import get_settings

MAX_LOGO_BYTES = 1_000_000
_MAGIC = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp"}


def save_image(upload: UploadFile, tenant_id: uuid.UUID, prefix: str) -> str:
    data = upload.file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image must be under 1 MB")
    ext = next((e for magic, e in _MAGIC.items() if data.startswith(magic)), None)
    if ext == "webp" and data[8:12] != b"WEBP":
        ext = None
    if ext is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a PNG, JPG or WebP image")
    s = get_settings()
    folder = Path(s.upload_dir) / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}-{uuid.uuid4().hex[:12]}.{ext}"
    (folder / name).write_bytes(data)
    # A path on the same address the app is opened from, so pictures load both on the restaurant's
    # Wi-Fi (Wise Box) and through the public address. TODO(phase 4): S3 + CDN for the hosted version.
    return f"/uploads/{tenant_id}/{name}"




MAX_AD_IMAGE = 5 * 1024 * 1024
MAX_AD_VIDEO = 60 * 1024 * 1024


def save_ad(upload: UploadFile, tenant_id: uuid.UUID) -> tuple[str, str]:
    """A customer-screen ad: PNG/JPG/WebP picture (5 MB) or MP4/WebM video (60 MB). Returns (kind, url)."""
    data = upload.file.read(MAX_AD_VIDEO + 1)
    if len(data) > MAX_AD_VIDEO:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Videos must be under 60 MB. Export it at 1080p or smaller.")
    ext = next((e for magic, e in _MAGIC.items() if data.startswith(magic)), None)
    if ext == "webp" and data[8:12] != b"WEBP":
        ext = None
    kind = "image"
    if ext is None:
        if data[4:8] == b"ftyp":
            ext, kind = "mp4", "video"
        elif data.startswith(b"\x1a\x45\xdf\xa3"):
            ext, kind = "webm", "video"
        else:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a PNG, JPG or WebP picture, or an MP4 video")
    elif len(data) > MAX_AD_IMAGE:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Pictures must be under 5 MB")
    s = get_settings()
    folder = Path(s.upload_dir) / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"ad-{uuid.uuid4().hex[:12]}.{ext}"
    (folder / name).write_bytes(data)
    return kind, f"/uploads/{tenant_id}/{name}"


def remove_upload(url: str, tenant_id: uuid.UUID) -> None:
    prefix = f"/uploads/{tenant_id}/"
    if not url.startswith(prefix) or "/" in url[len(prefix):] or ".." in url:
        return
    try:
        (Path(get_settings().upload_dir) / str(tenant_id) / url[len(prefix):]).unlink(missing_ok=True)
    except OSError:
        pass
