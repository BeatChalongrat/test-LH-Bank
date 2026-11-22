"""Bounded previews of private uploads and internally generated presentations."""

import base64
import io
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pptx import Presentation

MAX_PDF_PREVIEW = 96 * 1024


def pdf_thumbnail(raw):
    """Rasterize page one so the browser never opens an active PDF from an upload."""
    command = shutil.which("pdftoppm")
    if not command:
        return None
    with TemporaryDirectory(prefix="desk-preview-") as directory:
        original = Path(directory, "preview.pdf")
        output = original.with_suffix(".jpg")
        original.write_bytes(raw)
        try:
            subprocess.run(
                [
                    command,
                    "-l",
                    "1",
                    "-singlefile",
                    "-scale-to",
                    "920",
                    "-jpeg",
                    str(original),
                    str(original.with_suffix("")),
                ],
                timeout=8,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={
                    key: os.environ[key]
                    for key in ("SYSTEMROOT", "WINDIR", "PATH", "LANG")
                    if key in os.environ
                },
            )
            if output.stat().st_size <= MAX_PDF_PREVIEW:
                return base64.b64encode(output.read_bytes()).decode("ascii")
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def attachment_view(file):
    payload = file["payload"]
    if table := payload.get("table"):
        return {
            "kind": "table",
            "headers": table["headers"],
            "rows": table["rows"][:25],
            "row_count": len(table["rows"]),
        }
    return {
        "kind": "document",
        "image": bool(payload.get("thumbnail")),
        "sections": [
            {"label": label, "text": body[:1500]}
            for label, body in payload["blocks"][:10]
        ],
    }


def slide_view(binary):
    """Read the actual downloadable deck. These templates use text, tables and one series."""
    deck = Presentation(io.BytesIO(binary))
    slides = []
    for slide in deck.slides:
        text, table, points = [], None, []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                text.append(shape.text)
            elif shape.has_table:
                table = [[cell.text for cell in row.cells] for row in shape.table.rows]
            elif shape.has_chart:
                plot = shape.chart.plots[0]
                points = [
                    {"label": category.label, "value": value}
                    for category, value in zip(plot.categories, plot.series[0].values)
                ]
        # The generated deck has a title followed by optional text and a source footer.
        slides.append(
            {
                "title": text[0] if text else "",
                "text": text[1:-1],
                "source": text[-1] if len(text) > 1 else "",
                "table": table,
                "points": points,
            }
        )
    return {"kind": "slides", "slides": slides}
