"""Create local Compose secrets once, without printing them or replacing existing configuration."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
(root / "var/compose").mkdir(parents=True, exist_ok=True)
try:
    with os.fdopen(
        os.open(root / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as f:
        for key in ("DB_PASSWORD", "WEB_DB_PASSWORD"):
            f.write(f"{key}={secrets.token_urlsafe(36)}\n")
except FileExistsError:
    print("Using existing .env")
existing = (root / ".env").read_text().splitlines()
with (root / ".env").open("a") as f:
    for key in ("METRICS_TOKEN", "BROWSER_SIGNING_KEY"):
        if not any(line.startswith(key + "=") for line in existing):
            f.write(f"\n{key}={secrets.token_urlsafe(36)}\n")
