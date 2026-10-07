"""Backups: everything an install has, in one encrypted file, and putting it
back on a fresh install.

A backup is a tar archive encrypted with OpenPGP under a passphrase the owner
chooses (``gpg --symmetric``, AES-256), so it opens without Flatout too:

    gpg --decrypt flatout-backup-2026-10-07-1612.tar.gpg | tar -x

Inside: ``flatout-backup.json`` first (what made it), ``flatout.db`` (a
consistent copy taken with SQLite's backup API while the site runs), and the
rest of DATA_DIR: repo/, gnupg/ (the signing key), media/, bundles/ and the
generated secret files. Left out: scratch space, earlier backups and restores
in progress.

Making one is a job (jobs.HANDLERS["backup"]), so it never runs alongside a
change to the repository. The passphrase never touches the disk: it waits in
memory for its job (jobs.enqueue's ``secret``).

Restoring happens in the setup wizard of a fresh install. The file arrives in
pieces of CHUNK_BYTES, small enough for a proxy such as Cloudflare's, which
refuses request bodies over 100 MB; each piece is retried on its own. Once the
first piece is in, the passphrase and the manifest are checked against it, so
a wrong passphrase shows before the rest uploads. The whole file is then
decrypted and unpacked beside the data, gpg confirms it is intact, and only
then does it replace the fresh install's data.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path

from flask import Flask, current_app

from .models import db, end_worker_transaction, utcnow
from .repo import RepoError

FORMAT = 1
MANIFEST = "flatout-backup.json"
DATABASE = "flatout.db"
SUFFIX = ".tar.gpg"
CHUNK_BYTES = 90 * 1024 * 1024
MIN_PASSPHRASE = 12

# Never in a backup, never replaced by a restore.
SKIP = {"staging", "backups", "restore", DATABASE, f"{DATABASE}-wal", f"{DATABASE}-shm", f"{DATABASE}-journal"}


class BackupError(RepoError):
    """Something the person backing up or restoring can act on. A RepoError,
    so a backup job's log shows it as written."""


def data_dir() -> Path:
    return Path(current_app.config["DATA_DIR"])


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(exist_ok=True)
    return path


def restore_dir() -> Path:
    return data_dir() / "restore"


def database_path() -> Path:
    url = db.engine.url
    if url.get_backend_name() != "sqlite" or not url.database:
        raise BackupError("Backups need the built-in SQLite database; this install uses another (DATABASE_URL).")
    return Path(url.database)


def gpg_available() -> bool:
    return shutil.which("gpg") is not None


def human(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def version_core(version: str) -> tuple[int, ...]:
    """X.Y.Z as numbers; a pre-release suffix doesn't change the schema."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0])[:3])


# ———————————————————————————— gpg ————————————————————————————

class _Gpg:
    """gpg in a throwaway home of its own (the repository's keyring stays
    out of it), with the passphrase handed over on a pipe, never in argv or
    on disk."""

    def __init__(self, passphrase: str):
        self.home = tempfile.mkdtemp(prefix="flatout-gpg-")
        self.passphrase = passphrase.encode()
        self.stderr = tempfile.TemporaryFile()

    def popen(self, args: list[str], **kw) -> subprocess.Popen:
        read, write = os.pipe()
        os.write(write, self.passphrase)
        os.close(write)
        try:
            return subprocess.Popen(
                ["gpg", "--batch", "--yes", "--no-tty", "--pinentry-mode", "loopback",
                 "--passphrase-fd", str(read), *args],
                env=dict(os.environ, GNUPGHOME=self.home, LC_ALL="C.UTF-8"),
                pass_fds=(read,), stderr=self.stderr, **kw)
        finally:
            os.close(read)

    def errors(self) -> str:
        self.stderr.seek(0)
        return self.stderr.read().decode(errors="replace")

    def close(self) -> None:
        subprocess.run(["gpgconf", "--homedir", self.home, "--kill", "gpg-agent"], capture_output=True)
        shutil.rmtree(self.home, ignore_errors=True)
        self.stderr.close()


def _decrypt_error(gpg: _Gpg) -> BackupError:
    text = gpg.errors().lower()
    if "bad session key" in text or "bad passphrase" in text or "checksum error" in text:
        return BackupError("The passphrase doesn't open this backup.")
    if "no valid openpgp data" in text or "unknown packet" in text or "no data" in text:
        return BackupError("This file isn't a Flatout backup.")
    return BackupError("The backup is damaged and can't be restored: " + (gpg.errors().strip().splitlines() or ["gpg failed"])[-1])


# ———————————————————————————— Making one ————————————————————————————

def latest() -> dict | None:
    """The backup ready to download, if there is one."""
    files = sorted(backups_dir().glob("flatout-backup-*" + SUFFIX))
    if not files:
        return None
    path = files[-1]
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "created_at": _iso(stat.st_mtime)}


def _iso(timestamp: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _contents() -> list[Path]:
    return sorted(p for p in data_dir().iterdir() if p.name not in SKIP)


def _size_of(paths: list[Path]) -> int:
    total = 0
    for top in paths:
        if top.is_file():
            total += top.stat().st_size
        elif top.is_dir():
            for root, _dirs, files in os.walk(top):
                for name in files:
                    try:
                        total += os.lstat(os.path.join(root, name)).st_size
                    except OSError:
                        pass
    return total


def _snapshot(dest: Path, job_id: int | None) -> None:
    """A consistent copy of the live database. The job making the backup is
    marked finished in the copy, or a restore would show it still running."""
    source = sqlite3.connect(database_path())
    copy = sqlite3.connect(dest)
    try:
        source.backup(copy)
        if job_id is not None:
            copy.execute("UPDATE jobs SET status = 'done', finished_at = ?, log = log || ? WHERE id = ?",
                         (utcnow().isoformat(sep=" "), "\nThis is the backup's own job.", job_id))
            copy.commit()
    finally:
        copy.close()
        source.close()


def create(passphrase: str, lines: list, job_id: int | None = None) -> Path:
    """Write a new backup into backups/, replacing the previous one."""
    if not gpg_available():
        raise BackupError("gpg isn't installed here. Run Flatout from its Docker image.")
    contents = _contents()
    estimate = _size_of(contents) + database_path().stat().st_size
    free = shutil.disk_usage(backups_dir()).free
    if free < estimate * 1.1 + 50 * 1024 * 1024:
        raise BackupError(f"Not enough free disk space: the backup needs about {human(estimate)}, "
                          f"and {human(free)} is free.")
    from . import __version__
    from .models import get_setting
    manifest = json.dumps({
        "format": FORMAT, "version": __version__,
        "created_at": utcnow().isoformat() + "Z",
        "public_url": get_setting("public_url") or "",
    }, indent=2).encode()
    end_worker_transaction()   # the archive can take a while; don't hold the database

    final = backups_dir() / f"flatout-backup-{utcnow():%Y-%m-%d-%H%M%S}{SUFFIX}"
    partial = final.with_name(final.name + ".partial")
    snapshot = backups_dir() / f".snapshot-{secrets.token_hex(4)}.db"
    lines.append(f"Backing up about {human(estimate)}.")
    gpg = _Gpg(passphrase)
    try:
        _snapshot(snapshot, job_id)
        proc = gpg.popen(["--symmetric", "--cipher-algo", "AES256", "--compress-algo", "none",
                          "--s2k-digest-algo", "SHA512", "--s2k-count", "65011712",
                          "--output", str(partial)], stdin=subprocess.PIPE)
        count = 0
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                info = tarfile.TarInfo(MANIFEST)
                info.size, info.mtime, info.mode = len(manifest), int(utcnow().timestamp()), 0o644
                tar.addfile(info, io.BytesIO(manifest))
                tar.add(snapshot, arcname=DATABASE)
                for path in contents:
                    def keep(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
                        nonlocal count
                        count += 1
                        return member
                    tar.add(path, arcname=path.name, filter=keep)
        except BrokenPipeError:
            pass   # gpg stopped early; its exit status says why
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        if proc.wait() != 0:
            raise BackupError("gpg couldn't encrypt the backup: " + gpg.errors().strip()[-300:])
        partial.replace(final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        snapshot.unlink(missing_ok=True)
        gpg.close()
    for old in backups_dir().glob("flatout-backup-*" + SUFFIX):
        if old != final:
            old.unlink(missing_ok=True)
    lines.append(f"Wrote {final.name}: {count} files, {human(final.stat().st_size)}, encrypted with AES-256.")
    return final


def delete() -> bool:
    found = False
    for path in backups_dir().glob("flatout-backup-*" + SUFFIX):
        path.unlink(missing_ok=True)
        found = True
    return found


# ———————————————————————————— Restoring ————————————————————————————
# An upload in progress lives in restore/<id>/: backup.part as it arrives,
# then data/ as it unpacks. State that only matters while the process runs
# (restoring, done, failed) is kept in memory.

_state: dict[str, dict] = {}
_state_lock = threading.Lock()
ID_RE = re.compile(r"^[a-f0-9]{24}$")


def _upload_dir(upload_id: str) -> Path:
    if not ID_RE.match(upload_id or ""):
        raise BackupError("No such restore. Start again.")
    path = restore_dir() / upload_id
    if not path.is_dir():
        raise BackupError("No such restore. Start again.")
    return path


def start_upload(size: int) -> dict:
    if not gpg_available():
        raise BackupError("gpg isn't installed here. Run Flatout from its Docker image.")
    if size <= 0:
        raise BackupError("The backup file is empty.")
    # Room for the file and for what it unpacks to.
    free = shutil.disk_usage(data_dir()).free
    if free < size * 2.1 + 50 * 1024 * 1024:
        raise BackupError(f"Not enough free disk space to restore a {human(size)} backup: "
                          f"it needs about {human(int(size * 2.1))}, and {human(free)} is free.")
    with _state_lock:
        if any(s.get("state") == "restoring" for s in _state.values()):
            raise BackupError("A restore is already running.")
        shutil.rmtree(restore_dir(), ignore_errors=True)
        _state.clear()
    upload_id = secrets.token_hex(12)
    path = restore_dir() / upload_id
    path.mkdir(parents=True)
    (path / "backup.part").touch()
    (path / "size").write_text(str(size))
    return {"id": upload_id, "chunk_size": CHUNK_BYTES, "received": 0}


def _expected(path: Path) -> int:
    return int((path / "size").read_text())


def received(upload_id: str) -> int:
    return (_upload_dir(upload_id) / "backup.part").stat().st_size


def add_chunk(upload_id: str, offset: int, stream, length: int | None) -> int:
    """Append a piece at ``offset``. A piece sent again (its answer was lost)
    is accepted without writing twice."""
    path = _upload_dir(upload_id)
    part = path / "backup.part"
    have = part.stat().st_size
    if length is None:
        raise BackupError("Send each piece with its length.")
    if length > CHUNK_BYTES:
        raise BackupError(f"Pieces can be {CHUNK_BYTES // (1024 * 1024)} MB at most.")
    if offset + length <= have:
        return have   # already here
    if offset != have:
        raise BackupError(f"Expected the piece at byte {have}.")
    if have + length > _expected(path):
        raise BackupError("That is more than the backup's size.")
    with open(part, "ab") as out:
        left = length
        while left:
            chunk = stream.read(min(left, 1024 * 1024))
            if not chunk:
                break
            out.write(chunk)
            left -= len(chunk)
        if left:
            out.truncate(have)
            raise BackupError("The piece was cut off. Send it again.")
    return have + length


def _read_manifest(tar: tarfile.TarFile) -> dict:
    first = tar.next()
    if first is None or first.name != MANIFEST:
        raise BackupError("This file isn't a Flatout backup.")
    try:
        manifest = json.loads(tar.extractfile(first).read())
    except (ValueError, AttributeError):
        raise BackupError("This file isn't a Flatout backup.")
    from . import __version__
    if manifest.get("format") != FORMAT:
        raise BackupError("This backup was made by a version of Flatout this one can't read.")
    if version_core(str(manifest.get("version", ""))) > version_core(__version__):
        raise BackupError(f"This backup is from Flatout {manifest.get('version')}, newer than this install "
                          f"({__version__}). Update this install first.")
    return manifest


def check(upload_id: str, passphrase: str) -> dict:
    """Open what has arrived so far: right passphrase, a Flatout backup, not
    from a newer Flatout. Answers with the manifest."""
    part = _upload_dir(upload_id) / "backup.part"
    if not part.stat().st_size:
        raise BackupError("Nothing has arrived yet.")
    gpg = _Gpg(passphrase)
    proc = gpg.popen(["--decrypt", str(part)], stdout=subprocess.PIPE)
    try:
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                return _read_manifest(tar)
        except (tarfile.TarError, EOFError):
            proc.kill()
            proc.wait()
            raise _decrypt_error(gpg)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        gpg.close()


def status(upload_id: str) -> dict:
    with _state_lock:
        state = dict(_state.get(upload_id) or {})
    if state:
        return state
    return {"state": "receiving", "received": received(upload_id)}


def finish(app: Flask, upload_id: str, passphrase: str, inline: bool = False) -> None:
    """Restore a fully uploaded backup, in a thread of its own (it can take
    minutes, longer than a proxy waits for an answer)."""
    path = _upload_dir(upload_id)
    if received(upload_id) != _expected(path):
        raise BackupError("The backup hasn't finished uploading.")
    with _state_lock:
        if any(s.get("state") == "restoring" for s in _state.values()):
            raise BackupError("A restore is already running.")
        _state[upload_id] = {"state": "restoring", "step": "Decrypting and unpacking the backup."}
    if inline:
        _restore_in_thread(app, upload_id, passphrase)
    else:
        threading.Thread(target=_restore_in_thread, args=(app, upload_id, passphrase),
                         daemon=True, name="flatout-restore").start()


def _restore_in_thread(app: Flask, upload_id: str, passphrase: str) -> None:
    with app.app_context():
        try:
            path = restore_dir() / upload_id
            restore_file(app, path / "backup.part", passphrase, work=path)
            shutil.rmtree(path, ignore_errors=True)
            result = {"state": "done"}
        except BackupError as err:
            result = {"state": "failed", "error": str(err)}
        except Exception as err:   # shown to the person restoring, and logged
            app.logger.exception("restore failed")
            result = {"state": "failed", "error": f"The restore failed: {type(err).__name__}: {err}"}
        with _state_lock:
            _state[upload_id] = result


def restore_file(app: Flask, backup: Path, passphrase: str, work: Path | None = None) -> dict:
    """Unpack ``backup`` and make it this install's data. Also what the
    ``flask restore-backup`` command runs."""
    if work is None:   # restore/ is left alone by the swap; scratch space must live there
        restore_dir().mkdir(exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="cli-", dir=restore_dir()))
    unpacked = work / "data"
    shutil.rmtree(unpacked, ignore_errors=True)
    unpacked.mkdir(parents=True)
    gpg = _Gpg(passphrase)
    proc = gpg.popen(["--decrypt", str(backup)], stdout=subprocess.PIPE)
    try:
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                manifest = _read_manifest(tar)
                for member in tar:   # starts over at the manifest, already read
                    if member.name == MANIFEST or member.name.split("/")[0] in SKIP - {DATABASE}:
                        continue
                    tar.extract(member, unpacked, filter="data")
        except tarfile.FilterError as err:
            raise BackupError(f"The backup holds a file it shouldn't ({err}); it wasn't restored.")
        except (tarfile.TarError, EOFError):
            proc.kill()
            proc.wait()
            raise _decrypt_error(gpg)
        # gpg checks the whole file's integrity only at the end.
        if proc.wait() != 0:
            raise _decrypt_error(gpg)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        gpg.close()
    if not (unpacked / DATABASE).is_file():
        raise BackupError("The backup has no database in it; it wasn't restored.")
    _swap_in(app, unpacked, work / "replaced")
    shutil.rmtree(work / "replaced", ignore_errors=True)
    shutil.rmtree(unpacked, ignore_errors=True)
    return manifest


def _swap_in(app: Flask, unpacked: Path, replaced: Path) -> None:
    """Replace this install's data with the unpacked backup's."""
    from . import _migrate, setup, stats
    live = data_dir()
    database = database_path()
    replaced.mkdir(parents=True, exist_ok=True)
    subprocess.run(["gpgconf", "--homedir", str(live / "gnupg"), "--kill", "gpg-agent"], capture_output=True)
    db.session.remove()
    db.engine.dispose()
    for name in (f"{DATABASE}-wal", f"{DATABASE}-shm", f"{DATABASE}-journal"):
        (database.parent / name).unlink(missing_ok=True)
    os.replace(unpacked / DATABASE, database)
    for item in _contents():
        shutil.move(str(item), str(replaced / item.name))
    for item in sorted(unpacked.iterdir()):
        shutil.move(str(item), str(live / item.name))
    db.engine.dispose()

    # What the process read from the old data at startup.
    keyfile = live / ".secret_key"
    if not os.environ.get("SECRET_KEY") and keyfile.exists():
        app.secret_key = app.config["SECRET_KEY"] = keyfile.read_text().strip()
    with stats._salt_lock:
        stats._salt = None
    setup._completed["done"] = False
    db.create_all()
    _migrate(app)
