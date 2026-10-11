"""AppImages: the update information they carry, and their zsync files.

An AppImage says where its updates come from inside its own runtime. In a
type 2 AppImage that is the ELF section .upd_info, 1024 bytes; a type 1
AppImage keeps 512 bytes at a fixed place in its ISO 9660 header. Gear Lever,
AppImageUpdate and AppImageLauncher read it. "zsync|<address>" points them at
a .zsync file, which gives the newest file's SHA-1 (an update is there when it
differs from the installed file's), its address, and block checksums so that
only the blocks that changed are fetched.

Flatout writes that line into every AppImage uploaded, pointing at the address
that always gives the newest zsync file of the AppImage's name and channel,
then makes the zsync file with zsyncmake from the file as served. The line is
plain text in a section made for it, so nothing else in the file changes. Two
things are left as they are: a signed AppImage, whose signature covers the
section, and the runtime's MD5 digest, which appimagetool computes over
uninitialized memory and nothing checks.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

from . import repo
from .repo import RepoError

SECTION = ".upd_info"
SIGNATURE = ".sha256_sig"
TYPE1_OFFSET, TYPE1_LENGTH = 33651, 512
# ELF e_machine values, as the architectures Flatout names elsewhere.
MACHINES = {0x3E: "x86_64", 0xB7: "aarch64", 0x28: "armhf", 0x03: "i686"}


def kind(head: bytes) -> int:
    """2 or 1 for an AppImage of that type, 0 for anything else. Both are
    ELF files with "AI" and the type in the padding of the ELF header."""
    if head[:4] == b"\x7fELF" and head[8:10] == b"AI" and len(head) > 10 and head[10] in (1, 2):
        return head[10]
    return 0


def _sections(f) -> dict[str, tuple[int, int]]:
    """The ELF section headers: name to (offset in the file, size)."""
    f.seek(0)
    header = f.read(64)
    if len(header) < 52:
        return {}
    bits64, end = header[4] == 2, "<" if header[5] == 1 else ">"
    if bits64:
        (shoff,), (entsize, count, names) = struct.unpack_from(end + "Q", header, 0x28), \
            struct.unpack_from(end + "HHH", header, 0x3A)
        entry = end + "IIQQQQ"
    else:
        (shoff,), (entsize, count, names) = struct.unpack_from(end + "I", header, 0x20), \
            struct.unpack_from(end + "HHH", header, 0x2E)
        entry = end + "IIIIII"
    if not shoff or not count or count > 4096 or names >= count or entsize < struct.calcsize(entry):
        return {}
    f.seek(shoff)
    table = f.read(entsize * count)
    if len(table) < entsize * count:
        return {}
    rows = [struct.unpack_from(entry, table, i * entsize) for i in range(count)]
    rows = [(name, offset, size) for name, _type, _flags, _addr, offset, size in rows]
    f.seek(rows[names][1])
    strings = f.read(min(rows[names][2], 1 << 20))
    out = {}
    for name, offset, size in rows:
        stop = strings.find(b"\0", name)
        if name < len(strings) and stop > name:
            out[strings[name:stop].decode("ascii", "replace")] = (offset, size)
    return out


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def read(path: Path) -> dict:
    """What an AppImage says about itself: its type, the architecture it
    runs on, its update information, whether it is signed, and how much
    room there is for update information. Empty for any other file."""
    with open(path, "rb") as f:
        head = f.read(20)
        found = kind(head)
        if not found:
            return {}
        machine = struct.unpack_from("<H" if head[5] == 1 else ">H", head, 0x12)[0]
        out = {"type": found, "arch": MACHINES.get(machine, ""), "update_info": "", "signed": False, "room": 0}
        if found == 1:
            f.seek(TYPE1_OFFSET)
            raw = f.read(TYPE1_LENGTH)
            out.update(update_info=_text(raw), room=TYPE1_LENGTH if len(raw) == TYPE1_LENGTH else 0)
            return out
        sections = _sections(f)
        if SECTION in sections:
            offset, size = sections[SECTION]
            f.seek(offset)
            out.update(update_info=_text(f.read(size)), room=size)
        if SIGNATURE in sections:
            offset, size = sections[SIGNATURE]
            f.seek(offset)
            out["signed"] = any(f.read(size))
        return out


def write_update_info(path: Path, info: str) -> None:
    """Put ``info`` in the AppImage's update information, the rest of the
    room zeroed, as appimagetool -u does."""
    data = info.encode()
    with open(path, "r+b") as f:
        found = kind(f.read(20))
        if found == 1:
            offset, size = TYPE1_OFFSET, TYPE1_LENGTH
        else:
            offset, size = _sections(f).get(SECTION, (0, 0))
        if not size:
            raise RepoError("This AppImage's runtime has no room for update information.")
        if len(data) >= size:
            raise RepoError(f"The update address is longer than the {size - 1} characters the AppImage has room for.")
        f.seek(offset)
        f.write(data + bytes(size - len(data)))


def update_info(zsync_url: str) -> str:
    return f"zsync|{zsync_url}"


# ———————————————————————————— zsync ————————————————————————————

def make_zsync(path: Path, log: list) -> Path:
    """Write <file>.zsync beside the file. Its URL is the file's own name,
    which a client resolves against wherever it fetched the zsync file;
    ``with_url`` gives a copy an absolute one."""
    dest = path.with_name(path.name + ".zsync")
    tmp = path.with_name(path.name + ".zsync.tmp")
    # -Z: an AppImage is no gzip file, but some hold one near the start, and
    # zsyncmake would otherwise describe that instead of the file itself.
    try:
        repo.run(["zsyncmake", "-Z", "-u", path.name, "-o", str(tmp), str(path)], log)
    except RepoError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dest)
    return dest


def with_url(data: bytes, url: str) -> bytes:
    """A zsync file with its URL header set to ``url``. The header is text up
    to the first blank line; the block checksums after it stay as they are."""
    head, sep, rest = data.partition(b"\n\n")
    lines = [b"URL: " + url.encode() if line.startswith(b"URL:") else line for line in head.split(b"\n")]
    return b"\n".join(lines) + sep + rest


def zsync_sha1(data: bytes) -> str:
    for line in data.partition(b"\n\n")[0].split(b"\n"):
        if line.startswith(b"SHA-1:"):
            return line[6:].strip().decode()
    return ""
