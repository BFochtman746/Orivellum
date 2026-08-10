"""XML surgery — the only way this system ever writes a workbook.

The rule that survives from the old doctrine: NEVER round-trip a deliverable
through a library save. openpyxl's writer discards cached values for external
links, VBA (without keep_vba), and whatever parts it does not model. Surgery
edits the exact bytes that need to change inside the zip and copies every
other part through untouched — and `parts_diff()` proves it, byte for byte.

Two operations, both mechanical and both re-checkable:

  reorder_sheet_xml   put <worksheet> children into the OOXML sequence that
                      iOS Excel Mobile enforces
  refresh_cached      replace a formula cell's saved <v> with the value a real
                      recalculation produced (or insert one where the file was
                      shipped never-calculated)
"""

import re
import shutil
import zipfile
from pathlib import Path

# Correct OOXML child order inside <worksheet> (subset that occurs in practice;
# anything unknown keeps its position at the tail, before the closing tag).
SHEET_ORDER = [
    "sheetPr",
    "dimension",
    "sheetViews",
    "sheetFormatPr",
    "cols",
    "sheetData",
    "sheetCalcPr",
    "sheetProtection",
    "protectedRanges",
    "scenarios",
    "autoFilter",
    "sortState",
    "dataConsolidate",
    "customSheetViews",
    "mergeCells",
    "phoneticPr",
    "conditionalFormatting",
    "dataValidations",
    "hyperlinks",
    "printOptions",
    "pageMargins",
    "pageSetup",
    "headerFooter",
    "rowBreaks",
    "colBreaks",
    "customProperties",
    "cellWatches",
    "ignoredErrors",
    "smartTags",
    "drawing",
    "legacyDrawing",
    "legacyDrawingHF",
    "picture",
    "oleObjects",
    "controls",
    "webPublishItems",
    "tableParts",
    "extLst",
]

# Any XML tag, comment, CDATA or PI — used by the top-level tokenizer.
_TOKEN = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|"
    r"<(/?)((?:\w+:)?[\w.-]+)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*)(/?)>",
    re.S,
)


def _split_worksheet(xml):
    m_open = re.search(r"<(?:\w+:)?worksheet(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>", xml)
    m_close = re.search(r"</(?:\w+:)?worksheet\s*>", xml)
    if not m_open or not m_close or m_close.start() < m_open.end():
        return None
    return xml[: m_open.end()], xml[m_open.end() : m_close.start()], xml[m_close.start() :]


def _top_level_children(inner):
    """Tokenize DIRECT children of <worksheet> only. Returns a list of
    (localname|None, block_text) — None marks comments/PIs/CDATA — or None
    when the content cannot be tokenized cleanly (surgery must then refuse)."""
    out, depth, start, name, pos = [], 0, None, None, 0
    for m in _TOKEN.finditer(inner):
        if m.start() > pos and depth == 0 and inner[pos : m.start()].strip():
            return None  # stray top-level text: refuse
        pos = m.end()
        if m.group(2) is None:  # comment / CDATA / PI
            if depth == 0:
                out.append((None, m.group(0)))
            continue
        # group 3 can swallow the trailing "/" of a self-closing tag, so
        # decide from the raw token text, not the capture group
        closing, tag = m.group(1) == "/", m.group(2)
        selfclose = m.group(0).endswith("/>")
        local = tag.split(":")[-1]
        if depth == 0 and not closing:
            start, name = m.start(), local
            if selfclose:
                out.append((name, inner[m.start() : m.end()]))
                start = None
            else:
                depth = 1
        elif not closing and not selfclose:
            depth += 1
        elif closing:
            depth -= 1
            if depth == 0:
                out.append((name, inner[start : m.end()]))
                start = None
            elif depth < 0:
                return None
    if depth != 0 or (pos < len(inner) and inner[pos:].strip()):
        return None
    return out


def sheet_order_violations(xml):
    """Which known children appear before one that must precede them."""
    parts = _split_worksheet(xml)
    if not parts:
        return []
    children = _top_level_children(parts[1])
    if children is None:
        # Fall back to a linear scan of known opening tags so detection still
        # reports, even where surgery would refuse to operate.
        seen = [
            m.group(1)
            for m in re.finditer(r"<(?:\w+:)?(" + "|".join(SHEET_ORDER) + r")(?=[\s/>])", xml)
        ]
    else:
        seen = [n for n, _ in children if n in SHEET_ORDER]
    out, last = [], -1
    for t in seen:
        i = SHEET_ORDER.index(t)
        if i < last:
            out.append(t)
        last = max(last, i)
    return sorted(set(out))


def reorder_sheet_xml(xml):
    """Return the sheet XML with <worksheet> children in canonical order —
    or the INPUT UNCHANGED when it will not restructure safely: unknown child
    elements, top-level comments, or content the tokenizer cannot account
    for. A refusal leaves the order violation standing, the proof gate fails,
    and the report says so — that is the honest outcome. Byte-identical input
    comes back byte-identical when there is nothing to fix."""
    if not sheet_order_violations(xml):
        return xml
    parts = _split_worksheet(xml)
    if not parts:
        return xml
    head, inner, tail = parts
    children = _top_level_children(inner)
    if children is None:
        return xml
    if any(n is None or n not in SHEET_ORDER for n, _ in children):
        return xml  # unknown content: never restructure it
    ordered = sorted(children, key=lambda c: SHEET_ORDER.index(c[0]))
    return head + "".join(c[1] for c in ordered) + tail


def _fmt_value(v):
    """Excel's lexical form for a cached value + the required t attribute."""
    if isinstance(v, bool):
        return ("1" if v else "0"), "b"
    if isinstance(v, (int, float)):
        f = float(v)
        if f == int(f) and abs(f) < 1e15:
            return str(int(f)), None
        return repr(f), None
    if isinstance(v, str):
        if v.startswith("#"):
            return v, "e"
        return _xml_escape(v), "str"
    return None, None


def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def refresh_cached(xml, updates):
    """updates: {coordinate: python_value}. Rewrites (or inserts) the <v> of
    each formula cell and fixes the cell's t attribute to match the new type.
    Only touches <c> elements that contain an <f> — inputs are never edited."""
    changed = []

    def fix_cell(m):
        cell = m.group(0)
        coord = m.group(1)
        if coord not in updates or "<f" not in cell:
            return cell
        # Shared-string and inline-string cells carry payloads this function
        # does not rewrite — leave them; an unrepaired mismatch fails the
        # gates honestly instead of risking a corrupt cell.
        if re.search(r'\bt="(?:s|inlineStr)"', cell) or "<is" in cell:
            return cell
        text, t = _fmt_value(updates[coord])
        if text is None:
            return cell
        new = re.sub(r"<v>.*?</v>|<v/>", "", cell, flags=re.S)
        new = re.sub(r"(</(?:\w+:)?f>|<(?:\w+:)?f[^>]*/>)", r"\1" + f"<v>{text}</v>", new, count=1)
        if new == cell and "<v>" not in cell:
            return cell  # could not place a value: skip
        # t attribute on the opening <c ...> tag
        open_tag = re.match(r"<(?:\w+:)?c\b[^>]*>", new).group(0)
        stripped = re.sub(r'\s+t="[^"]*"', "", open_tag)
        if t:
            stripped = stripped[:-1] + f' t="{t}">'
        new = stripped + new[len(open_tag) :]
        if new != cell:
            changed.append(coord)
        return new

    out = re.sub(
        r'<(?:\w+:)?c\b[^>]*\br="([A-Z]{1,3}\d{1,7})"[^>]*>.*?</(?:\w+:)?c>',
        fix_cell,
        xml,
        flags=re.S,
    )
    return out, changed


def sheet_part_names(target):
    """{sheet name: xl/worksheets/sheetN.xml} via workbook.xml + rels."""
    with zipfile.ZipFile(target) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8", errors="replace")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", errors="replace")
    rel_map = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    rel_map.update(dict((i, t) for t, i in re.findall(r'Target="([^"]+)"[^>]*Id="([^"]+)"', rels)))
    out = {}
    for m in re.finditer(r'<sheet\b[^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wb):
        tgt = rel_map.get(m.group(2), "")
        if tgt:
            out[_xml_unescape(m.group(1))] = "xl/" + tgt.lstrip("/").removeprefix("xl/")
    return out


def _xml_unescape(s):
    return (
        s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def apply(target, out_path, part_edits):
    """Write a copy of `target` at `out_path` with `part_edits`
    ({zip name: new bytes/str}) swapped in and EVERY other part copied through
    byte-for-byte. Compression settings preserved per entry."""
    target, out_path = Path(target), Path(out_path)
    if not part_edits:
        shutil.copyfile(target, out_path)
        return []
    touched = []
    with (
        zipfile.ZipFile(target) as zin,
        zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename in part_edits:
                new = part_edits[info.filename]
                data = new.encode("utf-8") if isinstance(new, str) else new
                touched.append(info.filename)
            # preserve entry metadata; recompress with the entry's own method
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zout.writestr(zi, data)
    return touched


def parts_diff(a, b):
    """Which zip parts differ between two workbooks — the proof that surgery
    touched only what it claimed. Returns {"changed":[...], "added":[...],
    "removed":[...]}."""

    def load(p):
        with zipfile.ZipFile(p) as z:
            return {n: z.read(n) for n in z.namelist()}

    pa, pb = load(a), load(b)
    return {
        "changed": sorted(n for n in pa.keys() & pb.keys() if pa[n] != pb[n]),
        "added": sorted(pb.keys() - pa.keys()),
        "removed": sorted(pa.keys() - pb.keys()),
    }
