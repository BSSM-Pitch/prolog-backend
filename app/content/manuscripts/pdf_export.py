"""Portable Korean manuscript PDF. Fonts ship with the app, including offline deployments."""

from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_FONT_LOCK = Lock()
FONT = "PrologNotoKR"


def to_pdf(parts: list[tuple[str | None, str]]) -> bytes:
    with _FONT_LOCK:
        if FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(
                TTFont(FONT, str(Path(__file__).with_name("fonts") / "NotoSansKR-Regular.ttf"))
            )
    body = ParagraphStyle(
        "Body",
        fontName=FONT,
        fontSize=10.5,
        leading=18,
        wordWrap="CJK",
        alignment=TA_LEFT,
        spaceAfter=5,
    )
    heading = ParagraphStyle(
        "Chapter",
        parent=body,
        fontSize=16,
        leading=24,
        spaceBefore=20,
        spaceAfter=14,
        keepWithNext=True,
    )
    story = []
    for title, text in parts:
        if title:
            story.append(Paragraph(escape(title), heading))
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            # Paragraph parses markup; manuscript angle brackets and ampersands remain literal.
            story.append(
                Paragraph(
                    escape(line.expandtabs(8)).replace(" ", "&#160;").replace("\u3000", "&#12288;"),
                    body,
                )
                if line.strip()
                else Spacer(1, 10)
            )
    if not story:
        story.append(Spacer(1, 1))
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=54,
        leftMargin=54,
        topMargin=54,
        bottomMargin=54,
        title="원고",
        author="Prolog",
    )

    def page_number(canvas, doc):  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont(FONT, 9)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(A4[0] / 2, 28, str(doc.page))
        canvas.restoreState()

    document.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return output.getvalue()
