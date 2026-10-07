"""The Flatpak repository on disk, and the commands that change it.

Layout under DATA_DIR:

    repo/      the OSTree repository visitors' Flatpak pulls from (/repo/)
    staging/   where a bundle is unpacked before it is signed into repo/
    gnupg/     the signing key
    bundles/   the uploaded .flatpak files, kept for the download button

How an upload becomes an update, the way `flatpak` itself does it:

1. ``flatpak build-import-bundle`` unpacks the bundle into staging/.
2. ``flatpak build-commit-from`` copies it into repo/ as
   ``app/<id>/<arch>/<channel>``, signed, with a fresh timestamp.
3. ``flatpak build-update-repo`` signs a new summary, writes static deltas
   from the previous build, and prunes old history.

Promotion and rollback are step 2 with a different source: the beta's
commit, or an older commit of the same channel. The fresh timestamp matters
there: Flatpak refuses an update older than what is installed, so a rollback
has to be a new commit with the old content.

Every function here runs in the job thread (jobs.py), one at a time; nothing
else writes to repo/.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import current_app

from .models import get_setting, set_setting

ARCHES = ("x86_64", "aarch64", "i386", "arm")


class RepoError(Exception):
    """A step failed; the message is for the person who started it."""


def data_dir() -> Path:
    return Path(current_app.config["DATA_DIR"])


def repo_dir() -> Path:
    return data_dir() / "repo"


def staging_dir() -> Path:
    return data_dir() / "staging"


def gnupg_dir() -> Path:
    path = data_dir() / "gnupg"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def bundles_dir() -> Path:
    path = data_dir() / "bundles"
    path.mkdir(exist_ok=True)
    return path


def tools() -> dict:
    """Which of the programs Flatout runs are installed. The Docker image has
    all three; a development checkout may not."""
    return {name: shutil.which(name) is not None for name in ("flatpak", "ostree", "gpg")}


def run(args: list[str], log: list[str] | None = None, timeout: int = 1800, stdin: bytes | None = None) -> str:
    """Run a command; return its output. A failure raises RepoError with the
    tool's own last lines, which usually say what was wrong."""
    env = dict(os.environ, GNUPGHOME=str(gnupg_dir()), LC_ALL="C.UTF-8")
    if log is not None:
        log.append("$ " + " ".join(args))
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, env=env, input=stdin)
    except FileNotFoundError:
        raise RepoError(f"{args[0]} isn't installed here. Run Flatout from its Docker image.")
    except subprocess.TimeoutExpired:
        raise RepoError(f"{args[0]} took longer than {timeout // 60} minutes and was stopped.")
    out = proc.stdout.decode(errors="replace")
    err = proc.stderr.decode(errors="replace")
    if log is not None:
        for line in (out + err).strip().splitlines()[-40:]:
            log.append("  " + line)
    if proc.returncode != 0:
        tail = " ".join((err or out).strip().splitlines()[-3:]) or f"exit status {proc.returncode}"
        raise RepoError(f"{args[0]} failed: {tail}")
    return out + err


# ———————————————————————————— The signing key ————————————————————————————

def key_fingerprint() -> str | None:
    """The key the repository is signed with, if one has been made or
    imported and is still in the keyring."""
    fpr = get_setting("signing_key")
    if not fpr:
        return None
    if not (gnupg_dir() / "pubring.kbx").exists() and not (gnupg_dir() / "pubring.gpg").exists():
        return None
    return fpr


def key_info() -> dict | None:
    fpr = key_fingerprint()
    if not fpr:
        return None
    try:
        out = run(["gpg", "--batch", "--with-colons", "--list-secret-keys", fpr])
    except RepoError:
        return None
    info = {"fingerprint": fpr, "user_id": "", "created": "", "algorithm": ""}
    for line in out.splitlines():
        f = line.split(":")
        if f[0] == "sec" and not info["created"]:
            info["created"] = f[5]
            info["algorithm"] = {"1": "RSA", "22": "EdDSA", "17": "DSA", "19": "ECDSA"}.get(f[3], f[3]) + (f" {f[2]}" if f[2] else "")
        if f[0] == "uid" and not info["user_id"]:
            info["user_id"] = f[9]
    return info


def generate_key(name: str, email: str, log: list | None = None) -> str:
    """A new RSA 4096 signing key with no passphrase (the server signs on its
    own) and no expiry. Returns its fingerprint."""
    uid = f"{name} <{email}>" if email else name
    run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
         "--quick-gen-key", uid, "rsa4096", "sign", "never"], log)
    out = run(["gpg", "--batch", "--with-colons", "--list-secret-keys", uid])
    fprs = [line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:")]
    if not fprs:
        raise RepoError("gpg made a key but didn't list it.")
    set_setting("signing_key", fprs[-1])
    return fprs[-1]


def import_key(armored: str, log: list | None = None) -> str:
    """Use an existing secret key. It must not have a passphrase: the server
    signs every update without anyone there to type one."""
    if "PRIVATE KEY BLOCK" not in armored:
        raise RepoError("Paste an exported secret key: gpg --armor --export-secret-keys <id>.")
    out = run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
               "--import-options", "import-show", "--with-colons", "--import"], log, stdin=armored.encode())
    fprs = [line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:")]
    if not fprs:
        raise RepoError("No key found in what was pasted.")
    fpr = fprs[0]
    # Sign something small to prove the key works without a passphrase.
    try:
        run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
             "--local-user", fpr, "--detach-sign", "--output", "/dev/null"], stdin=b"flatout")
    except RepoError:
        run(["gpg", "--batch", "--yes", "--delete-secret-and-public-key", fpr])
        raise RepoError("That key has a passphrase. Remove it first (gpg --passwd), then import it again.")
    set_setting("signing_key", fpr)
    return fpr


def public_key() -> bytes | None:
    fpr = key_fingerprint()
    if not fpr:
        return None
    try:
        proc = subprocess.run(["gpg", "--batch", "--export", fpr], capture_output=True, timeout=30,
                              env=dict(os.environ, GNUPGHOME=str(gnupg_dir())))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return proc.stdout or None


def public_key_base64() -> str:
    key = public_key()
    return base64.b64encode(key).decode() if key else ""


def public_key_armored() -> str:
    fpr = key_fingerprint()
    return run(["gpg", "--batch", "--armor", "--export", fpr]) if fpr else ""


def secret_key_armored() -> str:
    """For the owner's backup. Session-only in the API."""
    fpr = key_fingerprint()
    if not fpr:
        raise RepoError("There is no signing key yet.")
    return run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                "--armor", "--export-secret-keys", fpr])


def _gpg_args() -> list[str]:
    fpr = key_fingerprint()
    if not fpr:
        raise RepoError("The repository has no signing key. Create one under Signing and addresses.")
    return [f"--gpg-sign={fpr}", f"--gpg-homedir={gnupg_dir()}"]


# ———————————————————————————— The repository ————————————————————————————

def ensure_repo(path: Path | None = None, log: list | None = None) -> Path:
    path = path or repo_dir()
    if not (path / "config").exists():
        path.mkdir(parents=True, exist_ok=True)
        run(["ostree", "init", "--mode=archive-z2", f"--repo={path}"], log)
    return path


def refs(path: Path | None = None) -> dict[str, str]:
    path = path or repo_dir()
    if not (path / "config").exists():
        return {}
    out = run(["ostree", "refs", f"--repo={path}"])
    result = {}
    for ref in out.split():
        try:
            result[ref] = run(["ostree", "rev-parse", f"--repo={path}", ref]).strip()
        except RepoError:
            pass
    return result


def has_commit(commit: str) -> bool:
    try:
        run(["ostree", "show", f"--repo={repo_dir()}", commit])
        return True
    except RepoError:
        return False


def _metadata(repo: Path, ref: str) -> dict:
    """The [Application] group of a commit's /metadata file."""
    try:
        text = run(["ostree", "cat", f"--repo={repo}", ref, "/metadata"])
    except RepoError:
        return {}
    out, group = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            group = line.strip("[]")
        elif "=" in line and group == "Application":
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _version(repo: Path, ref: str, app_id: str) -> str:
    """The newest <release version="…"> in the app's AppStream file, if it
    ships one."""
    for path in (f"/files/share/metainfo/{app_id}.metainfo.xml", f"/files/share/metainfo/{app_id}.appdata.xml",
                 f"/files/share/appdata/{app_id}.appdata.xml", f"/files/share/appdata/{app_id}.metainfo.xml"):
        try:
            text = run(["ostree", "cat", f"--repo={repo}", ref, path])
        except RepoError:
            continue
        try:
            root = ET.fromstring(text.encode())
        except ET.ParseError:
            continue
        release = root.find("./releases/release")
        if release is not None and release.get("version"):
            return release.get("version")
    return ""


def import_bundle(bundle: Path, channel: str, log: list) -> dict:
    """Unpack a bundle and sign it into the repository as ``channel``.
    Returns what the bundle turned out to be."""
    staging = ensure_repo(staging_dir(), log)
    ensure_repo(log=log)
    gpg = _gpg_args()
    out = run(["flatpak", "build-import-bundle", "--no-update-summary", str(staging), str(bundle)], log)
    match = re.search(r"Importing (\S+) \(([0-9a-f]{64})\)", out)
    if not match:
        raise RepoError("flatpak didn't say what the bundle contains.")
    source_ref, source_commit = match.groups()
    kind, app_id, arch, branch = (source_ref.split("/") + ["", "", "", ""])[:4]
    if kind != "app":
        raise RepoError(f"The bundle holds {source_ref}, which isn't an application. Upload an app bundle.")
    meta = _metadata(staging, source_commit)
    version = _version(staging, source_commit, app_id)
    target = f"app/{app_id}/{arch}/{channel}"
    out = run(["flatpak", "build-commit-from", f"--src-repo={staging}", f"--src-ref={source_commit}",
               *gpg, "--update-appstream", "--no-update-summary", "--timestamp=NOW",
               str(repo_dir()), target], log)
    commit = _commit_from_output(out, target)
    _clear_staging(source_ref, log)
    return {"app_id": app_id, "arch": arch, "source_ref": source_ref, "commit": commit,
            "version": version, "runtime": meta.get("runtime", ""), "source_branch": branch}


def recommit(source: str, target_ref: str, log: list, end_of_life: str = "") -> str:
    """Copy a commit already in the repository (a ref or a checksum) to a
    ref, as a new signed commit stamped now. Used to promote and to roll back,
    and with ``end_of_life`` to retire a channel."""
    args = ["flatpak", "build-commit-from", f"--src-ref={source}", *_gpg_args(),
            "--update-appstream", "--no-update-summary", "--timestamp=NOW", "--force"]
    if end_of_life:
        args.append(f"--end-of-life={end_of_life}")
    out = run(args + [str(repo_dir()), target_ref], log)
    return _commit_from_output(out, target_ref)


def _commit_from_output(out: str, target: str) -> str:
    match = re.search(re.escape(target) + r": ([0-9a-f]{64})", out)
    if match:
        return match.group(1)
    return run(["ostree", "rev-parse", f"--repo={repo_dir()}", target]).strip()


def _clear_staging(ref: str, log: list) -> None:
    staging = staging_dir()
    try:
        run(["ostree", "refs", f"--repo={staging}", "--delete", ref], log)
        run(["ostree", "prune", f"--repo={staging}", "--refs-only", "--depth=0"], log)
    except RepoError:
        # Staging is scratch space: if pruning it fails, start it over.
        shutil.rmtree(staging, ignore_errors=True)


def update_summary(log: list, title: str = "", homepage: str = "", comment: str = "", icon: str = "") -> None:
    """Sign a new summary, write static deltas, prune history past the
    configured depth. Clients see the change after this."""
    ensure_repo(log=log)
    depth = get_setting("prune_depth") or "10"
    args = ["flatpak", "build-update-repo", *_gpg_args(), "--generate-static-deltas",
            "--prune", f"--prune-depth={depth}", "--default-branch=stable"]
    if title:
        args.append(f"--title={title}")
    if homepage:
        args.append(f"--homepage={homepage}")
    if comment:
        args.append(f"--comment={comment}")
    if icon:
        args.append(f"--icon={icon}")
    run(args + [str(repo_dir())], log, timeout=3600)


def size_bytes() -> int:
    total = 0
    for root, _dirs, files in os.walk(repo_dir()):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total
