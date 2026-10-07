"""The media library: uploaded images and fonts.

A file is stored once under DATA_DIR/media, named by the hash of its content,
so uploading the same file twice gives back the first one, and a cached copy
can never be stale. Its type is decided by its first bytes, not its name.
"""
from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

from flask import current_app

from .models import Media, db

IMAGE_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
    "svg": "image/svg+xml", "ico": "image/x-icon", "avif": "image/avif",
}
FONT_TYPES = {
    "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf",
}
MAX_IMAGE = 15 * 1024 * 1024
MAX_FONT = 5 * 1024 * 1024


class Rejected(Exception):
    pass


def media_dir() -> Path:
    path = Path(current_app.config["DATA_DIR"]) / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sniff(data: bytes) -> str | None:
    """The file's real type, from its first bytes."""
    head = data[:64]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if head[:4] == b"wOF2":
        return "woff2"
    if head[:4] == b"wOFF":
        return "woff"
    if head[:4] in (b"\x00\x01\x00\x00", b"true"):
        return "ttf"
    if head[:4] == b"OTTO":
        return "otf"
    text = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith(b"<svg") or (text.startswith(b"<?xml") and b"<svg" in text) or b"<svg" in text[:400]:
        return "svg"
    return None


def dimensions(kind: str, data: bytes) -> tuple[int | None, int | None]:
    """Width and height without an imaging library, for the formats that say
    so in their header. None where it can't tell."""
    try:
        if kind == "png":
            return struct.unpack(">II", data[16:24])
        if kind == "gif":
            return struct.unpack("<HH", data[6:10])
        if kind == "webp":
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return w, h
        if kind == "jpg":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + length
        if kind == "svg":
            text = data[:4096].decode("utf-8", "ignore")
            tag = re.search(r"<svg\b[^>]*>", text, re.S)
            if tag:
                attrs = tag.group(0)
                w = re.search(r'\bwidth="([\d.]+)(px)?"', attrs)
                h = re.search(r'\bheight="([\d.]+)(px)?"', attrs)
                if w and h:
                    return round(float(w.group(1))), round(float(h.group(1)))
                vb = re.search(r'\bviewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', attrs)
                if vb:
                    return round(float(vb.group(1))), round(float(vb.group(2)))
    except (struct.error, ValueError, IndexError):
        pass
    return None, None


def store(data: bytes, original_name: str, alt: str = "") -> Media:
    """Keep an uploaded file and return its library entry. Raises Rejected
    with a sentence for the person uploading."""
    if not data:
        raise Rejected("The file is empty.")
    ext = sniff(data)
    if ext in IMAGE_TYPES:
        kind, mime, limit = "image", IMAGE_TYPES[ext], MAX_IMAGE
    elif ext in FONT_TYPES:
        kind, mime, limit = "font", FONT_TYPES[ext], MAX_FONT
    else:
        raise Rejected("Upload an image (PNG, JPEG, WebP, GIF, SVG, AVIF, ICO) or a font (WOFF2, WOFF, TTF, OTF).")
    if len(data) > limit:
        raise Rejected(f"{'Images' if kind == 'image' else 'Fonts'} can be up to {limit // (1024 * 1024)} MB.")
    if ext == "svg" and re.search(rb"<script|\son\w+\s*=|javascript:", data, re.I):
        raise Rejected("SVG files with scripts in them can't be used.")

    digest = hashlib.sha256(data).hexdigest()
    filename = f"{digest[:20]}.{ext}"
    existing = Media.query.filter_by(filename=filename).first()
    if existing is not None:
        return existing
    (media_dir() / filename).write_bytes(data)
    width, height = dimensions(ext, data) if kind == "image" else (None, None)
    item = Media(filename=filename, original_name=(original_name or filename)[:255], kind=kind,
                 mime=mime, size=len(data), width=width, height=height, alt=(alt or "")[:300])
    db.session.add(item)
    db.session.commit()
    return item


def delete(item: Media) -> None:
    path = media_dir() / item.filename
    db.session.delete(item)
    db.session.commit()
    path.unlink(missing_ok=True)


def as_json(item: Media, used: set[str] | None = None) -> dict:
    out = {
        "id": item.id, "url": item.url, "kind": item.kind, "mime": item.mime,
        "name": item.original_name, "size": item.size, "width": item.width, "height": item.height,
        "alt": item.alt, "created_at": item.created_at.isoformat() + "Z",
    }
    if used is not None:
        out["in_use"] = item.url in used
    return out
