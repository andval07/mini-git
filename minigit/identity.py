"""Who is committing: env vars first (like git), then [user] in the git config files."""

import os
from datetime import datetime
from pathlib import Path

from minigit.errors import MiniGitError


def _config_user(paths: list[Path]) -> dict[str, str]:
    """Tiny reader for the [user] section; good enough for name/email."""
    found: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        section = ""
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("["):
                section = line.strip("[]").strip().lower()
            elif section == "user" and "=" in line:
                key, _, value = line.partition("=")
                found.setdefault(key.strip().lower(), value.strip())
    return found


def signature(kind: str, gitdir: Path, when: datetime | None = None) -> str:
    """Build "Name <email> <unix time> <+hhmm>" for kind "AUTHOR" or "COMMITTER"."""
    user = _config_user([gitdir / "config", Path.home() / ".gitconfig"])
    name = os.environ.get(f"GIT_{kind}_NAME") or os.environ.get("GIT_AUTHOR_NAME") or user.get("name")
    email = (
        os.environ.get(f"GIT_{kind}_EMAIL") or os.environ.get("GIT_AUTHOR_EMAIL") or user.get("email")
    )
    if not name or not email:
        raise MiniGitError("unknown identity: set GIT_AUTHOR_NAME and GIT_AUTHOR_EMAIL")

    moment = (when or datetime.now()).astimezone()
    offset = moment.strftime("%z")  # e.g. +0200
    return f"{name} <{email}> {int(moment.timestamp())} {offset}"
