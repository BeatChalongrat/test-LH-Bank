"""Bounded PDF extraction subprocess used by the document worker."""

import json
import os
import sys
from pathlib import Path

from .file_formats import MAX_TEXT, document_sections
from .ingest import split_blocks

MAX_PAGES = 80
MAX_BLOCKS = 400


def main():
    if os.name == "posix":
        import resource

        maximum_memory = 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (maximum_memory, maximum_memory))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    blocks = []
    chars = 0
    for location, content in document_sections(
        Path(sys.argv[1]), max_pdf_pages=MAX_PAGES
    ):
        chars += len(content)
        if chars > MAX_TEXT:
            raise ValueError("text_limit")
        for block in split_blocks(content):
            blocks.append((location, block))
            if len(blocks) > MAX_BLOCKS:
                raise ValueError("text_limit")
    if not blocks:
        raise ValueError("empty_pdf")
    print(json.dumps(blocks, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        code = str(error)
        if code not in {
            "encrypted_pdf",
            "page_limit",
            "text_limit",
            "empty_pdf",
            "file_limit",
        }:
            code = "unreadable_file"
        print(code, file=sys.stderr)
        sys.exit(1)
