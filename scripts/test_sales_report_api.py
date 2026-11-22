"""Exercise one real spreadsheet chat turn and verify the chart inside its downloaded deck."""

import argparse
import io
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
from openpyxl import Workbook
from pptx import Presentation

QUESTION = "ช่วยสรุปยอดขาย ทำกราฟ แล้วทำเป็น PowerPoint ให้หน่อย"
MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม"]
SALES = [20500, 21500, 43500]


def workbook():
    wb = Workbook()
    sheet = wb.active
    sheet.title = "ยอดขายไตรมาส 1"
    sheet.append(["เดือน", "สาขา", "ยอดขาย (บาท)", "จำนวนบิล"])
    for month, branch, amount, bills in [
        (0, "เหนือ", 18000, 18),
        (0, "ใต้", 2500, 3),
        (1, "เหนือ", 23000, 21),
        (1, "ใต้", -1500, 1),
        (2, "เหนือ", 28500, 28),
        (2, "ใต้", 15000, 12),
    ]:
        sheet.append([MONTHS[month], branch, amount, bills])
    sheet.append(["รวม", "", 85500, 83])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Server URL when using DESK_TEST_TOKEN")
    parser.add_argument("--out", type=Path, default=Path("var/sales-report-demo"))
    parser.add_argument(
        "--expect-planner", choices=["model", "built_in"], default="model"
    )
    args = parser.parse_args()
    token = os.getenv("DESK_TEST_TOKEN")
    if token and not args.base_url:
        parser.error("Supply --base-url with DESK_TEST_TOKEN")
    if not token:
        from hr_demo import compose, invite

        port = compose("port", "api", "8000")
        if not port.startswith("127.0.0.1:"):
            parser.error("Use an isolated local demo or set DESK_TEST_TOKEN")
        base = "http://" + port
        if args.base_url and args.base_url.rstrip("/") != base:
            parser.error("Local demo URL must match this Compose project")
    else:
        base = args.base_url.rstrip("/")
    args.out.mkdir(exist_ok=True, parents=True)
    raw = workbook()
    (args.out / "sales-q1.xlsx").write_bytes(raw)
    headers = {"Origin": base, "X-Desk-Request": "1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with httpx.Client(base_url=base, headers=headers, timeout=65) as client:
        if not token:
            exchange = client.post("/api/auth/invite", json={"code": invite("hr")})
            exchange.raise_for_status()
        path = "/api/conversations/" + str(uuid4())
        upload = client.post(
            path + "/attachments",
            params={"name": "sales-q1.xlsx"},
            content=raw,
            headers={"Content-Type": "application/octet-stream"},
        )
        upload.raise_for_status()
        payload = {
            "turn_id": str(uuid4()),
            "question": QUESTION,
            "attachment_id": upload.json()["id"],
        }
        stages = []
        result = None
        start = time.perf_counter()
        with client.stream("POST", path + "/stream", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                frame = json.loads(line)
                if frame["event"] == "error":
                    raise RuntimeError(frame["value"])
                if frame["event"] == "stage":
                    stages.append(frame["value"])
                if frame["event"] == "result":
                    result = frame["value"]
        elapsed = time.perf_counter() - start
        assert result and result["planner"] == args.expect_planner, result
        assert result["selected_tools"] == ["summarize", "chart", "powerpoint"], result
        assert result["summary"]["records"] == 6 and float(
            result["summary"]["total"]
        ) == sum(SALES), result["summary"]
        expected = list(zip(MONTHS, SALES))
        points = [(p["label"], p["value"]) for p in result["chart"]["points"]]
        assert points == expected, points
        url = result["artifact"]["url"]
        assert url == path + "/turns/" + payload["turn_id"] + "/export/pptx"
        download = client.get(url)
        download.raise_for_status()
        deck = Presentation(io.BytesIO(download.content))
        charts = [
            shape.chart
            for page in deck.slides
            for shape in page.shapes
            if shape.has_chart
        ]
        assert len(charts) == 1
        chart = charts[0]
        actual = list(
            zip([c.label for c in chart.plots[0].categories], chart.series[0].values)
        )
        assert actual == expected, actual
        assert len(download.content) == result["artifact"]["bytes"]
        (args.out / "sales-q1-report.pptx").write_bytes(download.content)
        receipt = {
            "question": QUESTION,
            "planner": result["planner"],
            "selected_tools": result["selected_tools"],
            "stages": stages,
            "summary": result["summary"],
            "chart_verified_in_pptx": points,
            "slides": len(deck.slides),
            "download_bytes": len(download.content),
            "http": {
                "upload": upload.status_code,
                "chat": response.status_code,
                "download": download.status_code,
            },
            "chat_elapsed_seconds": round(elapsed, 2),
            "trace_id": result["trace_id"],
        }
        (args.out / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
        )
        print(json.dumps(receipt, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
