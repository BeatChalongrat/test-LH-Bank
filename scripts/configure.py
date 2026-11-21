"""Create local Compose secrets once, without printing them or replacing existing configuration."""

import argparse
import base64
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

    if not any(line.startswith("PROVIDER_ENCRYPTION_KEY=") for line in existing):
        encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(
            "ascii"
        )
        f.write(f"\nPROVIDER_ENCRYPTION_KEY={encryption_key}\n")


parser = argparse.ArgumentParser()
parser.add_argument(
    "--admin-providers",
    action="store_true",
    help="Start without model weights and configure providers in the admin portal",
)
if parser.parse_args().admin_providers:
    config = dict(line.split("=", 1) for line in existing if "=" in line)
    additions = {
        "COMPOSE_PATH_SEPARATOR": "|",
        "COMPOSE_FILE": "compose.yaml|deploy/compose.admin-providers.yaml",
    }
    if any(key in config and config[key] != val for key, val in additions.items()):
        raise SystemExit(
            "An existing Compose selection is configured. Merge deploy/compose.admin-providers.yaml into that selection."
        )
    with (root / ".env").open("a") as f:
        for key, val in additions.items():
            if key not in config:
                f.write(f"\n{key}={val}\n")
