"""Large files sent in pieces.

A proxy in front of Flatout may cap request bodies (Cloudflare refuses
anything over 100 MB), so a large file, a bundle or a backup, goes up in
pieces of at most CHUNK_BYTES, each its own request, each retried on its own.

One upload is a folder under a base directory: ``part`` grows as pieces
arrive and ``size`` says how big it will be. A piece is sent with its offset;
one sent twice (its answer was lost on the way back) is taken once, and one
out of place is refused with how much has arrived, so the sender can carry on
from there. Uploads nobody finished are removed after a day (prune).
"""
from __future__ import annotations

import re
import secrets
import shutil
import time
from pathlib import Path

CHUNK_BYTES = 90 * 1024 * 1024
ID_RE = re.compile(r"^[a-f0-9]{24}$")


class ChunkError(Exception):
    """Written for the person uploading. ``status`` is the HTTP status to
    answer with; ``received`` is set when the sender should resume."""

    def __init__(self, message: str, status: int = 400, received: int | None = None):
        super().__init__(message)
        self.status = status
        self.received = received


def chunk_size(request_limit: int | None) -> int:
    """The piece size to ask for: CHUNK_BYTES, or less if the server's own
    request limit (MAX_CONTENT_LENGTH) is lower."""
    if not request_limit:
        return CHUNK_BYTES
    return min(CHUNK_BYTES, max(1024 * 1024, request_limit - 1024 * 1024))


def planned_total(base: Path, stalled_after_hours: int = 1) -> int:
    """Bytes the uploads in progress under ``base`` have still to receive, so
    a new one is refused when the pieces still to come wouldn't fit. What has
    arrived already takes its room on disk; an upload nothing has touched for
    an hour has stalled and isn't counted (prune removes it after a day)."""
    total = 0
    if not base.is_dir():
        return 0
    cutoff = time.time() - stalled_after_hours * 3600
    for path in base.iterdir():
        if not (path.is_dir() and ID_RE.match(path.name)):
            continue
        try:
            part = (path / "part").stat()
            if part.st_mtime >= cutoff:
                total += max(0, int((path / "size").read_text()) - part.st_size)
        except (OSError, ValueError):
            pass
    return total


def start(base: Path, size: int) -> str:
    upload_id = secrets.token_hex(12)
    path = base / upload_id
    path.mkdir(parents=True)
    (path / "part").touch()
    (path / "size").write_text(str(size))
    return upload_id


def folder(base: Path, upload_id: str) -> Path:
    path = base / upload_id if ID_RE.match(upload_id or "") else None
    if path is None or not path.is_dir():
        raise ChunkError("There is no upload with that id. Start it again.", 404)
    return path


def received(base: Path, upload_id: str) -> int:
    return (folder(base, upload_id) / "part").stat().st_size


def expected(base: Path, upload_id: str) -> int:
    return int((folder(base, upload_id) / "size").read_text())


def state(base: Path, upload_id: str) -> dict:
    return {"id": upload_id, "received": received(base, upload_id), "size": expected(base, upload_id)}


def add(base: Path, upload_id: str, offset: int, stream, length: int | None) -> int:
    """Append a piece at ``offset``; returns how much has arrived."""
    path = folder(base, upload_id)
    part = path / "part"
    have = part.stat().st_size
    if length is None:
        raise ChunkError("Send each piece with its length (Content-Length).", 411)
    if length > CHUNK_BYTES:
        raise ChunkError(f"Pieces can be {CHUNK_BYTES // (1024 * 1024)} MB at most.", 413)
    if offset + length <= have:
        return have   # this piece is already here
    if offset != have:
        raise ChunkError(f"Expected the piece at byte {have}.", 409, received=have)
    if have + length > int((path / "size").read_text()):
        raise ChunkError("That is more than the size the upload started with.", 400, received=have)
    with open(part, "ab") as out:
        left = length
        while left:
            data = stream.read(min(left, 1024 * 1024))
            if not data:
                break
            out.write(data)
            left -= len(data)
        if left:
            out.truncate(have)
            raise ChunkError("The piece was cut off. Send it again.", 409, received=have)
    return have + length


def complete_file(base: Path, upload_id: str) -> Path:
    """The finished file; refuses one still arriving."""
    have, size = received(base, upload_id), expected(base, upload_id)
    if have != size:
        raise ChunkError(f"The upload isn't finished: {have} of {size} bytes have arrived.", 409, received=have)
    return folder(base, upload_id) / "part"


def take(base: Path, upload_id: str, dest: Path) -> Path:
    """Move the finished file to ``dest`` and forget the upload."""
    shutil.move(str(complete_file(base, upload_id)), str(dest))
    discard(base, upload_id)
    return dest


def discard(base: Path, upload_id: str) -> None:
    shutil.rmtree(folder(base, upload_id), ignore_errors=True)


def prune(base: Path, max_age_hours: int = 24) -> int:
    """Remove uploads nothing has touched for a day. Returns how many."""
    if not base.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in base.iterdir():
        if not path.is_dir():
            continue
        newest = max((p.stat().st_mtime for p in path.rglob("*")), default=path.stat().st_mtime)
        if newest < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed
