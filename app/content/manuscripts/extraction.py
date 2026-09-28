"""텍스트 추출 포트.

라이브러리를 추가하지 않는다. `docx` 는 zip 안의 XML 이라 stdlib 로 충분하다 —
서식이 아니라 **본문 텍스트만** 필요하기 때문이다. 포트로 감싸 두었으므로 나중에
pdf 나 더 정확한 파서가 필요해지면 구현만 갈아끼운다.

**챕터로 쪼개지 않는다.** 명세에 분할 규칙이 없다(어디서 끊을지 정한 문장이 없다).
본문만 채우고 챕터는 사용자가 만든다.
"""

import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from typing import Protocol

# **지원 형식의 단일 출처다.** 발급 허용 목록과 추출 가능 목록을 두 곳에 두면 또 어긋난다 —
# 실제로 pdf 가 발급은 되는데 추출에서 반드시 실패하는 상태였다.
# ASSUMPTION: 명세 §1.4 는 UNSUPPORTED_FILE_FORMAT 만 두고 지원 목록을 적지 않는다.
# pdf 를 넣으려면 파서를 먼저 붙이고 이 튜플에 추가한다.
SUPPORTED_FORMATS = ("txt", "docx")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class UnsupportedFormat(Exception):
    """추출기가 다루지 못하는 형식. 잡의 error 로 기록된다."""


class Extractor(Protocol):
    def extract(self, file_format: str, data: bytes) -> str:
        """원본 바이트에서 본문 텍스트를 뽑는다."""
        ...


class FileExtractor:
    def extract(self, file_format: str, data: bytes) -> str:
        if file_format not in SUPPORTED_FORMATS:
            raise UnsupportedFormat(file_format)
        if file_format == "txt":
            return data.decode("utf-8", errors="replace")
        return _docx_text(data)


def _docx_text(data: bytes) -> str:
    """문단(`w:p`) 을 줄로, 문단 안의 런(`w:t`) 을 이어 붙인다."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        document = archive.read("word/document.xml")
    root = ET.fromstring(document)
    paragraphs = [
        "".join(node.text or "" for node in paragraph.iter(f"{_W}t"))
        for paragraph in root.iter(f"{_W}p")
    ]
    return "\n".join(paragraphs)


_extractor = FileExtractor()


def extractor() -> Extractor:
    return _extractor
