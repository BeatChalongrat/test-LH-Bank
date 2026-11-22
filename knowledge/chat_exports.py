"""Generate portable files from an authorized, committed chat turn."""

import csv
import io
import re

from .db import ROOT

FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


def clean(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value))


def safe_cell(value):
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_turn(fmt, title, result):
    title = clean(title)
    answer = clean(result["answer"])
    source = clean(result["attribution"])
    table = result.get("table")
    rows = (
        [[clean(v) for v in row] for row in [table["headers"], *table["rows"]]]
        if table
        else []
    )
    notes = ["Source / ที่มา · " + source]
    for s in result.get("sources", []):
        notes.append(
            " · ".join(str(s[k]) for k in ("title", "version", "location") if s.get(k))
        )
    buf = io.BytesIO()
    if fmt == "md":
        lines = ["# " + title, "", answer, ""]
        if rows:
            escaped = [
                [v.replace("|", r"\|").replace("\n", " ") for v in r] for r in rows
            ]
            lines += [
                "| " + " | ".join(escaped[0]) + " |",
                "| " + " | ".join("---" for _ in escaped[0]) + " |",
            ]
            lines += ["| " + " | ".join(r) + " |" for r in escaped[1:]]
        return ("\n".join(lines + ["", *notes]) + "\n").encode()
    if fmt in {"csv", "xlsx"}:
        data = rows or [["Question", "Answer", "Source"], [title, answer, source]]
        if fmt == "csv":
            string = io.StringIO(newline="")
            csv.writer(string).writerows([[safe_cell(v) for v in row] for row in data])
            return string.getvalue().encode("utf-8-sig")
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        numeric = [
            i + 1 for i, h in enumerate(data[0]) if h in result.get("metrics", [])
        ]
        for rid, row in enumerate(data, 1):
            for cid, value in enumerate(row, 1):
                cell = ws.cell(rid, cid)
                if rid > 1 and cid in numeric and value.strip():
                    cell.value = float(value.replace(",", ""))
                else:
                    cell.value = value
                    cell.data_type = "s"
        if numeric:
            from openpyxl.chart import BarChart, Reference

            chart = BarChart()
            chart.title = data[0][numeric[0] - 1]
            chart.add_data(
                Reference(
                    ws, min_col=numeric[0], min_row=1, max_row=min(len(data), 21)
                ),
                titles_from_data=True,
            )
            labels = next(
                (i for i in range(1, len(data[0]) + 1) if i not in numeric), None
            )
            if labels:
                chart.set_categories(
                    Reference(ws, min_col=labels, min_row=2, max_row=min(len(data), 21))
                )
            wb.create_sheet("Chart").add_chart(chart, "A1")
        for cell in ws[1]:
            cell.font = Font(bold=True, color="28688A")
            ws.column_dimensions[cell.column_letter].width = 26
        ws.freeze_panes = "A2"
        sheet = wb.create_sheet("Source")
        for i, note in enumerate(notes, 1):
            cell = sheet.cell(i, 1, clean(note))
            cell.data_type = "s"
        wb.save(buf)
    elif fmt == "pdf":
        from fpdf import FPDF

        p = FPDF()
        p.set_margins(18, 16, 18)
        p.set_auto_page_break(True, 16)
        p.add_page()
        p.add_font("Thai", fname=str(ROOT / "static/assets/noto-sans-thai.ttf"))
        p.set_text_shaping(True)

        def line(text, size):
            p.set_font("Thai", size=size)
            p.set_x(p.l_margin)
            p.multi_cell(
                0, size * 0.55, text, wrapmode="CHAR", new_x="LMARGIN", new_y="NEXT"
            )

        p.set_text_color(27, 84, 115)
        line(title, 17)
        p.ln(4)
        p.set_text_color(25, 40, 50)
        line(answer, 11)
        if rows:
            p.ln(4)
            for r in rows:
                line("   |   ".join(r), 9)
        p.ln(5)
        for note in notes:
            line(note, 9)
        return bytes(p.output())
    elif fmt == "docx":
        from docx import Document

        doc = Document()
        doc.add_heading(title, 0)
        for line in answer.splitlines():
            doc.add_paragraph(line)
        if rows:
            t = doc.add_table(rows=0, cols=len(rows[0]))
            t.style = "Light Shading Accent 1"
            for row in rows:
                for cell, v in zip(t.add_row().cells, row):
                    cell.text = v
        for n in notes:
            doc.add_paragraph(n)
        doc.save(buf)
    elif fmt == "pptx":
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor

        deck = Presentation()
        deck.slide_width = Inches(13.33)
        deck.slide_height = Inches(7.5)

        def slide(heading, parts):
            page = deck.slides.add_slide(deck.slide_layouts[6])
            for txt, x, y, w, h, size in [
                (heading, 0.7, 0.42, 12, 1, 28),
                ("\n".join(parts), 0.75, 1.6, 11.8, 4.7, 18),
                (" · ".join(notes), 0.75, 6.6, 11.9, 0.6, 10),
            ]:
                box = page.shapes.add_textbox(
                    Inches(x), Inches(y), Inches(w), Inches(h)
                )
                tf = box.text_frame
                tf.word_wrap = True
                tf.text = txt
                for para in tf.paragraphs:
                    para.font.name = "Noto Sans Thai"
                    para.font.size = Pt(size)
                    para.font.color.rgb = RGBColor.from_string(
                        "28546B" if size != 18 else "263843"
                    )
            return page

        # Bound content per slide, including Thai passages without word separators.
        lines = [
            line[i : i + 75]
            for line in answer.splitlines()
            for i in range(0, len(line), 75)
        ] or [answer]
        for i in range(0, len(lines), 7):
            slide(title[:70], lines[i : i + 7])
        if rows:
            for col in range(0, len(rows[0]), 4):
                for start in range(1, min(len(rows), 31), 6):
                    page = slide("ข้อมูล / Data", [])
                    data = [rows[0][col : col + 4]] + [
                        r[col : col + 4] for r in rows[start : start + 6]
                    ]
                    t = page.shapes.add_table(
                        len(data),
                        len(data[0]),
                        Inches(0.75),
                        Inches(1.5),
                        Inches(11.8),
                        Inches(4.8),
                    ).table
                    for i, row in enumerate(data):
                        for j, v in enumerate(row):
                            cell = t.cell(i, j)
                            cell.text = v[:45] + ("…" if len(v) > 45 else "")
                            cell.text_frame.paragraphs[0].font.size = Pt(14)
            if len(rows) > 31:
                slide(
                    "ข้อมูลเพิ่มเติม / Additional rows",
                    [
                        "สไลด์แสดงข้อมูล 30 แถวแรก ดาวน์โหลด Excel หรือ CSV เพื่อดูข้อมูลทั้งหมด",
                        "Slides show the first 30 rows. Export Excel or CSV for the complete table.",
                    ],
                )
        deck.save(buf)
    else:
        raise ValueError("Unknown format")
    return buf.getvalue()
