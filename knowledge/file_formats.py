"""Bounded, read-only extraction from business document formats."""

import csv
import io
import posixpath
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from zipfile import ZipFile

from .ingest import sections

FORMATS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}
OFFICE = {".docx", ".xlsx", ".pptx"}
MAX_XML = 32 * 1024 * 1024
MAX_TEXT = 260_000


def tag(node):
    return node.tag.rsplit("}", 1)[-1]


def text_runs(node):
    return "".join(part.text or "" for part in node.iter() if tag(part) == "t")


def related_parts(archive, main):
    path = PurePosixPath(main)
    relations = str(path.parent / "_rels" / (path.name + ".rels"))
    mapping = {}
    for item in ET.fromstring(archive.read(relations)):
        if item.get("TargetMode") == "External":
            continue
        target = item.attrib["Target"]
        mapping[item.attrib["Id"]] = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join(str(path.parent), target))
        )
    for node in ET.fromstring(archive.read(main)).iter():
        for key, value in node.attrib.items():
            if key.endswith("}id") and value in mapping:
                yield node, mapping[value]


def sheet_rows(root, shared):
    for row in root.iter():
        if tag(row) != "row":
            continue
        fields = []
        for cell in row:
            kind = cell.get("t")
            value = next((part.text or "" for part in cell if tag(part) == "v"), "")
            if kind == "s" and value:
                value = shared[int(value)]
            elif kind == "inlineStr":
                value = text_runs(cell)
            if value:
                fields.append(value)
        if fields:
            yield " | ".join(fields)


def office_sections(archive, suffix):
    infos = archive.infolist()
    if (
        len(infos) > 1000
        or sum(info.file_size for info in infos) > MAX_XML
        or any(info.flag_bits & 1 for info in infos)
    ):
        raise ValueError("file_limit")
    if suffix == ".docx":
        root = ET.fromstring(archive.read("word/document.xml"))
        yield (
            "เนื้อหา Word",
            "\n".join(text_runs(p) for p in root.iter() if tag(p) == "p"),
        )
    elif suffix == ".pptx":
        for number, (node, part) in enumerate(
            (
                (node, part)
                for node, part in related_parts(archive, "ppt/presentation.xml")
                if tag(node) == "sldId"
            ),
            1,
        ):
            root = ET.fromstring(archive.read(part))
            yield (
                f"สไลด์ {number}",
                "\n".join(text_runs(p) for p in root.iter() if tag(p) == "p"),
            )
    else:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [
                text_runs(item)
                for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ]
        for node, part in related_parts(archive, "xl/workbook.xml"):
            if tag(node) != "sheet":
                continue
            root = ET.fromstring(archive.read(part))
            yield (
                "ชีต " + node.get("name", "")[:128],
                "\n".join(sheet_rows(root, shared)),
            )


def document_sections(path, max_pdf_pages):
    suffix = path.suffix
    if suffix == ".pdf":
        yield from sections(path, max_pdf_pages=max_pdf_pages)
    elif suffix in OFFICE:
        with ZipFile(path) as archive:
            yield from office_sections(archive, suffix)
    else:
        raw = path.read_bytes()
        encoding = (
            "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        )
        text = raw.decode(encoding)
        if "\x00" in text:
            raise ValueError("unreadable_file")
        if suffix == ".csv":
            text = "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(text)))
        yield "ข้อความ", text
