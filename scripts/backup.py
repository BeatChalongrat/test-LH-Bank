"""Write a verified custom-format dump from the Compose database, including on Windows."""

import datetime
import os
import subprocess
import tempfile
from pathlib import Path

base = Path(__file__).resolve().parents[1]
directory = base / "var/backups"
directory.mkdir(parents=True, exist_ok=True)
name = datetime.datetime.now(datetime.timezone.utc).strftime(
    "knowledge-%Y%m%dT%H%M%S%fZ.dump"
)
cmd = ["docker", "compose", "exec", "-T", "db"]
with tempfile.NamedTemporaryFile(dir=directory, delete=False) as f:
    temp = Path(f.name)
try:
    with temp.open("wb") as f:
        subprocess.run(
            cmd + ["pg_dump", "--format=custom", "-U", "desk_owner", "knowledge"],
            cwd=base,
            stdout=f,
            check=True,
        )
    with temp.open("rb") as f:
        subprocess.run(
            cmd + ["pg_restore", "--list"],
            cwd=base,
            stdin=f,
            stdout=subprocess.DEVNULL,
            check=True,
        )
    os.replace(temp, directory / name)
    print(directory / name)
finally:
    temp.unlink(missing_ok=True)
