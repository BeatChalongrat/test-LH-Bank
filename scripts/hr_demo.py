"""Set up, open or verify the local HR acceptance fixture with ordinary application APIs."""

import argparse
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

from open_workspace import ROOT, compose

USERS = {
    "hr": ("demo.hr", "employee"),
    "non-hr": ("demo.non-hr", "employee"),
    "admin": ("demo.admin", "admin"),
}
DOC = "DOC-8BCE6DBEFBDA42DC"


def invite(kind):
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
        *USERS[kind],
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
        raise RuntimeError("Could not issue an invitation")
    return code


def open_browser(base, kind):
    names = ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome")
    paths = [Path(p) for n in names if (p := shutil.which(n))]
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if folder := os.getenv(env):
            paths += [
                Path(folder) / sub
                for sub in (
                    "Microsoft/Edge/Application/msedge.exe",
                    "Google/Chrome/Application/chrome.exe",
                )
            ]
    paths += [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    route = "/admin" if kind == "admin" else "/"
    code = invite(kind)
    browser = next((p for p in paths if p.is_file()), None)
    if not browser:
        print(
            f"Open {base}{route} in a separate browser profile. Single-use sign-in code (10 minutes): {code}"
        )
        return
    profile = ROOT / "var" / "demo-profiles" / kind
    subprocess.Popen(
        [
            str(browser),
            "--user-data-dir=" + str(profile),
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            base + route + "#invite=" + code + ("&view=ask" if kind != "admin" else ""),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Opened {USERS[kind][0]} in its own browser profile")


def verify(base):
    for kind in ("hr", "non-hr"):
        client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

        def request(path, data=None):
            req = urllib.request.Request(
                base + path,
                data=json.dumps(data).encode() if data is not None else None,
                headers={
                    "Origin": base,
                    "Content-Type": "application/json",
                    "X-Desk-Request": "1",
                },
            )
            return client.open(req, timeout=60)

        with request("/api/auth/invite", {"code": invite(kind)}) as res:
            res.read()
        with request("/api/sources?" + urllib.parse.urlencode({"q": "ออร์คิด"})) as res:
            visible = any(row["doc_id"] == DOC for row in json.load(res))
        try:
            with request(f"/api/documents/{DOC}/original") as res:
                download = res.status if res.read(5) == b"%PDF-" else 0
        except urllib.error.HTTPError as exc:
            download = exc.code
        with request(
            f"/api/conversations/{uuid4()}/messages",
            {
                "question": "โครงการตรวจสุขภาพออร์คิดเบิกได้สูงสุดกี่บาทต่อปี",
                "turn_id": str(uuid4()),
            },
        ) as res:
            result = json.load(res)
        cited = DOC in [row["doc_id"] for row in result["sources"]]
        fact = "2750" in result["answer"].replace(",", "")
        expected = kind == "hr"
        status_ok = (
            (result["status"] == "answered")
            if expected
            else result["status"] in {"fallback", "refused"}
        )
        if not status_ok or (visible, download, cited, fact) != (
            expected,
            200 if expected else 404,
            expected,
            expected,
        ):
            raise SystemExit(
                f"{kind}: access or answer failed, trace {result['trace_id']}"
            )
        print(
            f"{kind}: library={visible}, original={download}, chat_cites_file={cited}, amount_visible={fact}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "verify", *USERS])
    action = parser.parse_args().action
    port = compose("port", "api", "8000")
    if not re.fullmatch(r"127\.0\.0\.1:\d+", port):
        sys.exit("Requires the local Compose API bound to IPv4 loopback")
    base = "http://" + port
    with urllib.request.urlopen(base + "/api/auth/options", timeout=5) as res:
        options = json.load(res)
    if options["organization"] or not options["invitation"]:
        sys.exit("Requires an isolated local installation with invitation sign-in")
    if action == "setup":
        print(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "seed",
                "python",
                "-m",
                "knowledge.hr_demo",
            )
        )
    elif action == "verify":
        verify(base)
    else:
        open_browser(base, action)


if __name__ == "__main__":
    main()
