"""A small Excel writer (.xlsx, Office Open XML SpreadsheetML) without an outside package: sheets of text and numbers
with a bold header row that stays in place, column widths, line breaks in long cells and a filter on the header.
A sheet may also be a page to read (results_tables.build_xlsx, "Übersicht"): a fifth entry of options switches the
fixed header and the filter off, gives rows a style of their own and makes cells jump to another sheet.

Text goes in as inline strings, never as a formula, so a cell starting with "=" stays text in Excel and LibreOffice.
First use: the category system of an analysis (app/transcript_coding_export.py, categories_xlsx)."""
import io
import re
import zipfile
from xml.sax.saxutils import escape

CELL_MAX = 32767  # the most characters Excel keeps in one cell
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")  # not allowed in XML 1.0

_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\
<Default Extension="xml" ContentType="application/xml"/>\
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\
{sheets}</Types>"""
_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\
</Relationships>"""
# 0: plain, 1: header (bold, wrapped, grey fill), 2: wrapped and at the top of the cell, 3: title (bold, larger),
# 4: a link to another sheet (blue)
PLAIN, HEADER, WRAPPED, TITLE, LINK = range(5)
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\
<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font>\
<font><b/><sz val="16"/><name val="Calibri"/></font><font><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/></font></fonts>\
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>\
<fill><patternFill patternType="solid"><fgColor rgb="FFEDEBE6"/><bgColor indexed="64"/></patternFill></fill></fills>\
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>\
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>\
<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>\
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>\
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>\
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top"/></xf>\
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top"/></xf></cellXfs>\
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>\
</styleSheet>"""


def column(n):
    """The letters of the n-th column (0: A, 26: AA)."""
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _cell(ref, value, style):
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if isinstance(value, bool):
        value = str(value)
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(_BAD.sub("", str(value))[:CELL_MAX])
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(header, rows, widths, options=None):
    """options: {"page": True} for a page to read (no fixed header row, no filter, the first row styled as the
    others), "styles": {row number from 1: style}, "links": {cell as "A7": name of the sheet it jumps to},
    "merge": ["A3:C3", ...] for cells laid over several columns and "heights": {row number: points} (Excel does not
    fit the height of a row to a merged cell by itself)."""
    rows = list(rows)
    options = options or {}
    page, styles, links = bool(options.get("page")), options.get("styles") or {}, options.get("links") or {}
    merge, heights = options.get("merge") or [], options.get("heights") or {}
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    view = ('<sheetView workbookViewId="0"/>' if page else
            '<sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView>')
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           f'<sheetViews>{view}</sheetViews><sheetFormatPr defaultRowHeight="15"/>']
    if cols:
        out.append(f"<cols>{cols}</cols>")
    out.append("<sheetData>")
    for r, row in enumerate([header] + rows, 1):
        style = styles.get(r, WRAPPED if page or r > 1 else HEADER)
        cells = "".join(_cell(f"{column(i)}{r}", v, LINK if f"{column(i)}{r}" in links else style)
                        for i, v in enumerate(row))
        height = f' ht="{heights[r]}" customHeight="1"' if r in heights else ""
        out.append(f'<row r="{r}"{height}>{cells}</row>')
    out.append("</sheetData>")
    if header and not page:
        out.append(f'<autoFilter ref="A1:{column(len(header) - 1)}{len(rows) + 1}"/>')
    if merge:
        out.append(f'<mergeCells count="{len(merge)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merge)
                   + "</mergeCells>")
    if links:  # a jump inside the file: no relationship, no address outside
        out.append("<hyperlinks>" + "".join(
            '<hyperlink ref="{}" location="{}"/>'.format(
                ref, escape("'" + name.replace("'", "''") + "'!A1", {'"': "&quot;"}))
            for ref, name in links.items()) + "</hyperlinks>")
    out.append("</worksheet>")
    return "".join(out)


def workbook(sheets):
    """The bytes of an .xlsx file. `sheets`: [(name, header, rows, widths)] or with options as a fifth entry
    (_sheet), widths in characters."""
    names = []
    for name, *_rest in sheets:  # at most 31 characters, none of []:*?/\ , unique
        clean = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
        while clean in names:
            clean = clean[:28] + f" {len(names) + 1}"
        names.append(clean)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _TYPES.format(sheets="".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1))))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                             for i, n in enumerate(names, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_name, header, rows, widths, *options) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet(header, rows, widths, options[0] if options else None))
    return buf.getvalue()
