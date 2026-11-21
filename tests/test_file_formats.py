from zipfile import ZipFile

import pytest

from knowledge.file_formats import document_sections

REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@pytest.mark.parametrize(
    ("ext", "parts", "location", "fact"),
    [
        (
            ".docx",
            {
                "word/document.xml": "<document><body><p><r><t>Word policy</t></r></p></body></document>"
            },
            "เนื้อหา Word",
            "Word policy",
        ),
        (
            ".xlsx",
            {
                "xl/workbook.xml": f'<workbook xmlns:r="{REL}"><sheets><sheet name="Rates" r:id="rId1"/></sheets></workbook>',
                "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>',
                "xl/sharedStrings.xml": "<sst><si><t>Meal</t></si></sst>",
                "xl/worksheets/sheet1.xml": '<worksheet><sheetData><row><c t="s"><v>0</v></c><c><v>200</v></c></row></sheetData></worksheet>',
            },
            "ชีต Rates",
            "Meal | 200",
        ),
        (
            ".pptx",
            {
                "ppt/presentation.xml": f'<presentation xmlns:r="{REL}"><sldIdLst><sldId r:id="rId5"/></sldIdLst></presentation>',
                "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="rId5" Target="slides/slide2.xml"/></Relationships>',
                "ppt/slides/slide2.xml": "<sld><p><r><t>Slide policy</t></r></p></sld>",
            },
            "สไลด์ 1",
            "Slide policy",
        ),
    ],
)
def test_ooxml_extracts_text_and_position(tmp_path, ext, parts, location, fact):
    file = tmp_path / ("example" + ext)
    with ZipFile(file, "w") as archive:
        for name, xml in parts.items():
            archive.writestr(name, xml)
    assert list(document_sections(file, 80)) == [(location, fact)]


@pytest.mark.parametrize(
    ("ext", "source", "output"),
    [
        (".txt", "ข้อความจากไฟล์", "ข้อความจากไฟล์"),
        (".md", "# วันลา", "# วันลา"),
        (".csv", "ประเภท,จำนวน\nวันลา,3", "ประเภท | จำนวน\nวันลา | 3"),
    ],
)
def test_extracts_text_formats(tmp_path, ext, source, output):
    file = tmp_path / ("example" + ext)
    file.write_text(source, encoding="utf-8-sig")
    assert list(document_sections(file, 80)) == [("ข้อความ", output)]
