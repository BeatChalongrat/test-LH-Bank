"""Open the loopback deployment using a personal session on this workstation."""

import argparse
import getpass
import json
import re
import subprocess
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def main(admin=False):
    port = compose("port", "api", "8000").splitlines()[0]
    if not re.fullmatch(r"127\.0\.0\.1:\d+", port):
        raise SystemExit("The desktop launcher requires an API bound to IPv4 loopback.")
    base = "http://" + port
    with urllib.request.urlopen(base + "/api/auth/options", timeout=5) as response:
        options = json.load(response)
    if options["organization"]:
        webbrowser.open(base + "/auth/login")
        return
    if not options["invitation"]:
        raise SystemExit(
            "Contact your identity administrator to enable organization sign-in."
        )
    # This operation requires local Docker administration. The web server cannot mint invitations.
    name = (
        re.sub(r"[^a-z0-9._-]+", ".", getpass.getuser().lower()).strip(".")
        or "employee"
    )
    role = "admin" if admin else "employee"
    account = ("ops." if admin else "desktop.") + name[:54]
    code = compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "seed",
        "python",
        "-m",
        "knowledge",
        "issue-invite",
        account,
        role,
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
        raise SystemExit("The portal could not issue a personal invitation.")
    path = "/admin" if admin else "/"
    if not webbrowser.open(base + path + "#invite=" + code):
        raise SystemExit(
            "Open the portal and ask your administrator for a one-time invitation."
        )
    print("Opened", role, "workspace at", base + path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Open the AI operations console for the local Docker operator",
    )
    main(parser.parse_args().admin)
