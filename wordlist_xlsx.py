"""Excel (.xlsx) import/export and the downloadable template for the Live
Translate word list.

Built on the standard library only (an .xlsx file is a zip of XML parts), so
the app gets no extra dependency to package. The table is three columns:

    A  word or name
    B  often heard as (optional, separated by commas)
    C  translate as (optional; empty = keep the word exactly as written)

The FIRST sheet is the list; a header row is skipped. The template has a
second sheet with examples and tips - it is never read back, so the examples
cannot be imported by accident.
"""

import re
import zipfile
from xml.sax.saxutils import escape as _escape

from wordlist import clean_entries

# Used to recognise (and skip) a header row, in the app's two languages.
HEADER_WORDS = {"word", "word or name", "words", "term", "name",
                "詞彙或名稱", "詞彙", "名稱"}

EXAMPLE_ENTRIES = [
    {"word": "Acme Dynamics", "heard": ["Acne Dynamics", "Acme Dinamics"], "translation": "艾克米動力"},
    {"word": "Dr. Okafor", "heard": ["Dr. Okafo", "doctor Okafor"], "translation": ""},
    {"word": "Lumora", "heard": ["Lumera", "Lumara"], "translation": ""},
    {"word": "kanban", "heard": ["can ban", "con bun"], "translation": "看板"},
    {"word": "OKR", "heard": ["O K R", "okay are"], "translation": ""},
    {"word": "Zhang Wei", "heard": ["Chang Way", "Jang Wei"], "translation": "張偉"},
    {"word": "Q3 roadmap", "heard": ["queue three roadmap"], "translation": "第三季路線圖"},
    {"word": "Penang", "heard": ["Pen nang", "Peanang"], "translation": "檻城"},
]

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col_letter(i):
    letters, i = "", i + 1
    while i:
        i, rem = divmod(i - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell(ref, text, style):
    text = _XML_BAD.sub("", str(text))
    return (f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">'
            f'{_escape(text)}</t></is></c>')


def _sheet_xml(rows, widths):
    """rows: list of lists of str; the first row is the (frozen) header."""
    out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<worksheet xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}">',
           '<sheetViews><sheetView workbookViewId="0">'
           '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
           '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/>',
           "<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" style="3" customWidth="1"/>'
                              for i, w in enumerate(widths)) + "</cols>",
           "<sheetData>"]
    for r, row in enumerate(rows, 1):
        style = 1 if r == 1 else 3
        cells = "".join(_cell(f"{_col_letter(c)}{r}", v, style) for c, v in enumerate(row))
        out.append(f'<row r="{r}">{cells}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


_STYLES_XML = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{_NS_MAIN}">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF3A6EA5"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
    '<alignment vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    # style 3 = text format, so Excel never turns "Q3" or "1/2" into a date or number
    '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def write_xlsx(path, entries, header, examples=None, tips=None,
               sheet_names=("Word list", "Examples and tips")):
    """Writes the word list as an Excel file. `header` = the 3 column titles.
    With `examples` (and `tips`: lines of text) a second sheet is added - that
    is the downloadable template."""
    rows = [list(header)] + [[e["word"], ", ".join(e["heard"]), e["translation"]]
                             for e in clean_entries(entries)]
    sheets = [(sheet_names[0], _sheet_xml(rows, (30, 46, 40)))]
    if examples is not None:
        ex_rows = [list(header)] + [[e["word"], ", ".join(e["heard"]), e["translation"]]
                                    for e in clean_entries(examples)]
        if tips:
            ex_rows += [[""]] + [[t] for t in tips]
        sheets.append((sheet_names[1], _sheet_xml(ex_rows, (30, 46, 40))))
    n = len(sheets)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                  for i in range(1, n + 1)) + "</Types>")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>')
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}">'
        "<sheets>" + "".join(f'<sheet name="{_escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
                              for i, (name, _x) in enumerate(sheets, 1)) + "</sheets></workbook>")
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                  f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
        + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES_XML)
        for i, (_name, xml) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _col_index(ref):
    n = 0
    for ch in re.match(r"[A-Za-z]+", ref).group(0).upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    """Reads the word list from the FIRST sheet of an .xlsx file (written by
    write_xlsx, Excel, LibreOffice or Google Sheets). A header row is skipped.
    Raises ValueError for a file that is not a readable workbook."""
    import xml.etree.ElementTree as ET
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as err:
        raise ValueError(f"not an Excel (.xlsx) file: {err}")
    with z:
        names = set(z.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("not an Excel (.xlsx) file")
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
                shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rel_id = next((sh.attrib.get(f"{{{_NS_REL}}}id") for sh in wb.iter() if _local(sh.tag) == "sheet"), None)
        target = "xl/worksheets/sheet1.xml"
        if rel_id and "xl/_rels/workbook.xml.rels" in names:
            for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                if rel.attrib.get("Id") == rel_id:
                    t = rel.attrib["Target"].lstrip("/")
                    target = t if t.startswith("xl/") else "xl/" + t
                    break
        if target not in names:
            raise ValueError("the workbook has no readable sheet")
        rows = []
        for row in ET.fromstring(z.read(target)).iter():
            if _local(row.tag) != "row":
                continue
            cells = {}
            for c in row:
                if _local(c.tag) != "c":
                    continue
                kind = c.attrib.get("t")
                if kind == "inlineStr":
                    text = "".join(t.text or "" for t in c.iter() if _local(t.tag) == "t")
                else:
                    v = next((x.text for x in c if _local(x.tag) == "v"), "") or ""
                    text = shared[int(v)] if kind == "s" and v.isdigit() and int(v) < len(shared) else v
                cells[_col_index(c.attrib.get("r", "A1"))] = text.strip()
            rows.append([cells.get(i, "") for i in range(3)])
    if rows and rows[0][0].strip().lower() in HEADER_WORDS:
        rows = rows[1:]
    return clean_entries([{"word": r[0], "heard": r[1], "translation": r[2]} for r in rows])
