"""Dependency-free DOCX and PDF writers for tailored CVs.

The project deliberately avoids adding ``python-docx``/ReportLab.  A CV only
needs a small, predictable subset of OOXML and PDF, so both formats are written
directly.  The output is ATS-readable: real text runs, no images, no text
boxes, no overlapping content, and every paragraph is measured before it is
placed on a PDF page.

Blocks are plain dictionaries so the tailoring service can build the same
content once and render it to either format::

    {"type": "name", "text": "Jane Ahmed"}
    {"type": "line", "text": "jane@example.com | Karachi"}
    {"type": "heading", "text": "Summary"}
    {"type": "paragraph", "text": "..."}
    {"type": "entry", "text": "Finance Manager — Acme", "meta": "2021 – Present"}
    {"type": "bullet", "text": "..."}
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from xml.sax.saxutils import escape as xml_escape

A4_WIDTH = 595.28
A4_HEIGHT = 841.89
MARGIN = 54.0
BODY_SIZE = 10.0
LEADING = 13.2

# Helvetica advance widths (per 1000 units) for the printable ASCII range.
_HELVETICA = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556, ":": 278, ";": 278,
    "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015, "A": 667, "B": 667,
    "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778, "P": 667,
    "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 278, "\\": 278, "]": 278, "^": 469,
    "_": 556, "`": 333, "a": 556, "b": 556, "c": 500, "d": 556, "e": 556,
    "f": 278, "g": 556, "h": 556, "i": 222, "j": 222, "k": 500, "l": 222,
    "m": 833, "n": 556, "o": 556, "p": 556, "q": 556, "r": 333, "s": 500,
    "t": 278, "u": 556, "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "{": 334, "|": 260, "}": 334, "~": 584,
}


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
_DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

_DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

_DOCX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="60" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
</w:styles>"""

_DOCX_CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{title}</dc:title>
  <dc:creator>JobPilot</dc:creator>
  <cp:lastModifiedBy>JobPilot</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>"""

_DOCX_APP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>JobPilot</Application>
</Properties>"""


def _docx_run(text: str, *, bold: bool = False, size: int = 21, color: str = "") -> str:
    props = [
        '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>',
        f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>',
    ]
    if bold:
        props.append("<w:b/><w:bCs/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    return (
        "<w:r><w:rPr>"
        + "".join(props)
        + f'</w:rPr><w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r>'
    )


def _docx_paragraph(
    runs: str,
    *,
    before: int = 0,
    after: int = 60,
    left: int = 0,
    hanging: int = 0,
    rule: bool = False,
) -> str:
    spacing = f'<w:spacing w:before="{before}" w:after="{after}" w:line="240" w:lineRule="auto"/>'
    indent = ""
    if left or hanging:
        indent = f'<w:ind w:left="{left}" w:hanging="{hanging}"/>'
    border = ""
    if rule:
        border = (
            '<w:pBdr><w:bottom w:val="single" w:sz="4" w:space="2" w:color="BFBFBF"/></w:pBdr>'
        )
    return f"<w:p><w:pPr>{spacing}{indent}{border}</w:pPr>{runs}</w:p>"


def _docx_blocks(blocks: list[dict]) -> str:
    paragraphs: list[str] = []
    for block in blocks:
        kind = block.get("type")
        text = str(block.get("text") or "").strip()
        if kind == "name":
            paragraphs.append(
                _docx_paragraph(_docx_run(text, bold=True, size=34), after=40)
            )
        elif kind == "line":
            paragraphs.append(
                _docx_paragraph(_docx_run(text, size=19, color="444444"), after=40)
            )
        elif kind == "heading":
            paragraphs.append(
                _docx_paragraph(
                    _docx_run(text.upper(), bold=True, size=22),
                    before=180,
                    after=70,
                    rule=True,
                )
            )
        elif kind == "entry":
            meta = str(block.get("meta") or "").strip()
            runs = _docx_run(text, bold=True, size=21)
            if meta:
                runs += _docx_run(f"  ·  {meta}", size=19, color="444444")
            paragraphs.append(_docx_paragraph(runs, before=80, after=20))
        elif kind == "bullet":
            paragraphs.append(
                _docx_paragraph(
                    _docx_run(f"•  {text}", size=21),
                    after=30,
                    left=260,
                    hanging=260,
                )
            )
        elif kind == "paragraph":
            if text:
                paragraphs.append(_docx_paragraph(_docx_run(text, size=21), after=50))
        elif kind == "spacer":
            paragraphs.append(_docx_paragraph("", after=60))
    body = "".join(paragraphs)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"{body}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" '
        'w:header="360" w:footer="360" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )


def build_docx(blocks: list[dict], *, title: str = "Tailored CV") -> bytes:
    """Build a valid, minimal DOCX package from content blocks."""
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = _docx_blocks(blocks)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _DOCX_STYLES)
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "docProps/core.xml",
            _DOCX_CORE.format(title=xml_escape(title), created=created),
        )
        archive.writestr("docProps/app.xml", _DOCX_APP)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _width(text: str, size: float) -> float:
    total = sum(_HELVETICA.get(char, 556) for char in text)
    return total * size / 1000.0


def _wrap_text(text: str, size: float, max_width: float) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        trial = f"{current} {word}".strip()
        if _width(trial, size) <= max_width or not current:
            current = trial
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    # Split pathological long tokens rather than letting them run past the margin.
    safe: list[str] = []
    for line in lines:
        while _width(line, size) > max_width and len(line) > 1:
            cut = len(line)
            while cut > 1 and _width(line[:cut], size) > max_width:
                cut -= 1
            safe.append(line[:cut])
            line = line[cut:]
        safe.append(line)
    return safe


def _pdf_escape(text: str) -> str:
    encoded = text.encode("cp1252", "replace").decode("latin-1")
    return encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _PdfPages:
    def __init__(self) -> None:
        self.pages: list[list[str]] = [[]]
        self.y = A4_HEIGHT - MARGIN

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = A4_HEIGHT - MARGIN

    def _ensure(self, height: float) -> None:
        if self.y - height < MARGIN:
            self._new_page()

    def line(
        self,
        text: str,
        *,
        size: float = BODY_SIZE,
        bold: bool = False,
        leading: float | None = None,
        indent: float = 0.0,
    ) -> None:
        leading = LEADING if leading is None else leading
        self._ensure(leading)
        font = "F2" if bold else "F1"
        x = MARGIN + indent
        self.pages[-1].append(
            f"BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {self.y:.2f} Tm "
            f"({_pdf_escape(text)}) Tj ET"
        )
        self.y -= leading

    def paragraph(
        self,
        text: str,
        *,
        size: float = BODY_SIZE,
        bold: bool = False,
        indent: float = 0.0,
        hanging: float = 0.0,
        leading: float | None = None,
    ) -> None:
        leading = LEADING if leading is None else leading
        max_width = A4_WIDTH - 2 * MARGIN - indent
        lines = _wrap_text(text, size, max_width)
        for index, line in enumerate(lines):
            self.line(
                line,
                size=size,
                bold=bold,
                leading=leading,
                indent=indent + (hanging if index else 0),
            )

    def space(self, height: float = 4.0) -> None:
        self.y -= height


def build_pdf(blocks: list[dict], *, title: str = "Tailored CV") -> bytes:
    """Build a paginated, text-selectable PDF using Helvetica metrics."""
    pages = _PdfPages()
    for block in blocks:
        kind = block.get("type")
        text = str(block.get("text") or "").strip()
        if kind == "name":
            pages.space(2)
            pages.line(text, size=18, bold=True, leading=23)
        elif kind == "line":
            pages.line(text, size=9.2, leading=13)
        elif kind == "heading":
            pages.space(7)
            pages.line(text.upper(), size=10.8, bold=True, leading=15)
            # A thin rule keeps sections legible without images or tables.
            pages._ensure(2)
            y = pages.y + 3
            pages.pages[-1].append(
                f"0.75 w 0.75 G {MARGIN:.2f} {y:.2f} m "
                f"{A4_WIDTH - MARGIN:.2f} {y:.2f} l S 0 G"
            )
            pages.space(3)
        elif kind == "entry":
            meta = str(block.get("meta") or "").strip()
            pages.space(2)
            pages.paragraph(text, size=10.2, bold=True, leading=13.2)
            if meta:
                pages.paragraph(meta, size=9.0, leading=12.0)
        elif kind == "bullet":
            pages.paragraph(f"•  {text}", size=BODY_SIZE, indent=14, hanging=14)
        elif kind == "paragraph":
            pages.paragraph(text, size=BODY_SIZE)
        elif kind == "spacer":
            pages.space(5)

    # Drop a trailing empty page created by an exact page-height fit.
    if len(pages.pages) > 1 and not pages.pages[-1]:
        pages.pages.pop()

    page_count = len(pages.pages)
    objects: dict[int, bytes] = {}
    catalog_id = 1
    pages_id = 2
    font_regular = 3
    font_bold = 4
    first_page = 5
    first_content = first_page + page_count
    page_ids = [first_page + index for index in range(page_count)]
    content_ids = [first_content + index for index in range(page_count)]

    objects[catalog_id] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("latin-1")
    )
    objects[font_regular] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    objects[font_bold] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
        b"/Encoding /WinAnsiEncoding >>"
    )

    for index, commands in enumerate(pages.pages):
        page_id = page_ids[index]
        content_id = content_ids[index]
        objects[page_id] = (
            "<< /Type /Page "
            f"/Parent {pages_id} 0 R "
            f"/MediaBox [0 0 {A4_WIDTH:.2f} {A4_HEIGHT:.2f}] "
            f"/Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("latin-1")
        stream = "\n".join(commands).encode("latin-1")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for object_id in range(1, max(objects) + 1):
        offsets[object_id] = buffer.tell()
        buffer.write(f"{object_id} 0 obj\n".encode("latin-1"))
        buffer.write(objects[object_id])
        buffer.write(b"\nendobj\n")
    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {max(objects) + 1}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for object_id in range(1, max(objects) + 1):
        buffer.write(f"{offsets[object_id]:010d} 00000 n \n".encode("latin-1"))
    buffer.write(
        (
            f"trailer\n<< /Size {max(objects) + 1} /Root {catalog_id} 0 R "
            f"/Info {catalog_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )
    return buffer.getvalue()


def validate_docx(data: bytes) -> list[str]:
    """Return human-readable problems; an empty list means the file is sound."""
    problems: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                problems.append("DOCX has no word/document.xml")
                return problems
            document = archive.read("word/document.xml").decode("utf-8")
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        return [f"DOCX cannot be opened: {exc}"]
    if "<w:body>" not in document:
        problems.append("DOCX document body is missing")
    if not re.search(r"<w:t[^>]*>[^<]+</w:t>", document):
        problems.append("DOCX contains no text")
    return problems


def validate_pdf(data: bytes) -> list[str]:
    """Validate structure with pypdf when available (it is a project dep)."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared
        return []
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        return [f"PDF cannot be opened: {exc}"]
    problems: list[str] = []
    if not reader.pages:
        problems.append("PDF has no pages")
    for index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            problems.append(f"PDF page {index + 1} cannot be read: {exc}")
            continue
        if not text.strip():
            problems.append(f"PDF page {index + 1} has no selectable text")
    return problems
