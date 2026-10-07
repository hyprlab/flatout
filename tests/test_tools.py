"""The repository tooling: the commit-msg hook and the documentation check.
These rules are what keep the history clean, so they are tested like code."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "tools" / "git-hooks" / "commit-msg"


def hook(tmp_path, message: str) -> int:
    msg = tmp_path / "msg"
    msg.write_text(message, encoding="utf-8")
    return subprocess.run([sys.executable, str(HOOK), str(msg)], capture_output=True).returncode


@pytest.mark.parametrize("message", [
    "feat(auth): add a sign-in throttle",
    "fix(db): keep deleted records deleted (#12)",
    "feat(auth): OAuth sign-in for Google",
    "chore(release): 1.4.0",
    "chore(release): 1.5.0-beta.1",
    "feat(api)!: rename the items endpoint",
    "docs: explain the backup command\n\nA paragraph on one line, explaining why.",
    "fix: credit a person\n\nCo-Authored-By: Jane Doe <1+jane@users.noreply.github.com>",
    "Merge branch 'main' into beta",
    'Revert "feat: something"',
])
def test_commit_messages_accepted(tmp_path, message):
    assert hook(tmp_path, message) == 0


@pytest.mark.parametrize("message", [
    "Add a sign-in throttle",
    "feat(auth): Add a sign-in throttle",
    "feat(auth): add a sign-in throttle.",
    "feat: " + "x" * 80,
    "fix: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    "fix: thing\n\nGenerated with Claude Code",
    "fix: thing\n\nClaude-Session: abc",
    "fix: thing — with a dash",
    "fix: thing\n\n" + "word " * 101,
    "Merge branch 'x'\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
])
def test_commit_messages_refused(tmp_path, message):
    assert hook(tmp_path, message) == 1


def test_documentation_check_passes():
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "check-docs.py")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
