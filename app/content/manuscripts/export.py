"""원고 내보내기 (MSU §4.11).

추출(`extraction.py`)과 같은 이유로 라이브러리를 추가하지 않는다 — docx 는 zip 안의 XML 이고
필요한 서식은 "장 제목은 제목 스타일, 나머지는 문단" 뿐이다.
"""

import re
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

from app.content.manuscripts.models import Chapter

EXPORT_FORMATS = ("txt", "docx")

CONTENT_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# 편집기는 "1장 첫 만남" 줄에서 "1장" 을 떼고 제목만 저장한다. 제목이 없으면 "1장" 자체가 남는다.
_NUMBERED = re.compile(r"^\s*(?:제\s*)?\d+\s*장")


def _heading(chapter: Chapter) -> str:
    title = (chapter.title or "").strip()
    if _NUMBERED.match(title):
        return title
    return f"{chapter.chapter_no}장 {title}".strip()


def sections(chapters: list[Chapter], content: str | None) -> list[tuple[str | None, str]]:
    """(장 제목, 본문) 목록. 챕터가 없으면 제목 없는 한 덩어리다."""
    if not chapters:
        return [(None, content or "")]
    ordered = sorted(chapters, key=lambda c: c.chapter_no)
    return [(_heading(c), c.content) for c in ordered]


def to_txt(parts: list[tuple[str | None, str]]) -> bytes:
    blocks = [f"{head}\n\n{body}" if head else body for head, body in parts]
    return "\n\n".join(blocks).encode("utf-8")


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_WML = "application/vnd.openxmlformats-officedocument.wordprocessingml"
_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/word/document.xml" ContentType="{_WML}.document.main+xml"/>'
    f'<Override PartName="/word/styles.xml" ContentType="{_WML}.styles+xml"/>'
    "</Types>"
)
_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{_R_NS}/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
_DOCUMENT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{_R_NS}/styles" Target="styles.xml"/>'
    "</Relationships>"
)
_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{_W_NS}">'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
    "</w:styles>"
)


def _paragraph(text: str, style: str | None = None) -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    run = f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else ""
    return f"<w:p>{props}{run}</w:p>"


def to_docx(parts: list[tuple[str | None, str]]) -> bytes:
    paragraphs: list[str] = []
    for head, body in parts:
        if head:
            paragraphs.append(_paragraph(head, "Heading1"))
        paragraphs.extend(_paragraph(line) for line in body.replace("\r\n", "\n").split("\n"))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>{"".join(paragraphs)}</w:body></w:document>'
    )
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", _RELS_XML)
        archive.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS_XML)
        archive.writestr("word/styles.xml", _STYLES_XML)
        archive.writestr("word/document.xml", document)
    return out.getvalue()


def render(file_format: str, chapters: list[Chapter], content: str | None) -> bytes:
    parts = sections(chapters, content)
    return to_docx(parts) if file_format == "docx" else to_txt(parts)
