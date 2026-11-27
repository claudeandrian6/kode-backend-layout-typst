# -*- coding: utf-8 -*-
"""Mesin analisis QA PDF mandiri untuk memeriksa hasil layout Scribus / PageMaker."""
from __future__ import annotations

import base64
import html
import io
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from PIL import Image, ImageDraw, ImageFont

from rules import CATEGORY_LABELS, Issue, JournalRules, QAReport

RE_CAPTION = re.compile(
    r"^(?:Table|Tabel|Figure|Gambar|Fig\.|Chart|Graph|Source|Sumber)\s*\d*[\.:]?",
    re.I,
)
RE_PAGE_NUM_ONLY = re.compile(r"^\d{1,4}$")
RE_CITE_OR_HDR = re.compile(
    r"^(?:To cite this article:|This is an open access article|ABSTRACT\b|Keywords?:|ISSN:|Homepage:)",
    re.I,
)
RE_RUNNING_FOOTER = re.compile(
    r"(?:^\d{1,4}\s*\||\|\s*\d{1,4}$|Bulletin of Social Studies|Pedagogy Review|Research in Education|IMRECS)",
    re.I,
)
RE_URL_META = re.compile(r"(?:https?://|www\.|doi:|issn:|@|\.org/|\.com/)", re.I)
RE_PROSE_LHS_STOP = re.compile(
    r"\b(?:is|are|was|were|be|been|with|that|this|those|these|which|for|from|into|"
    r"statistically|significant|significantly|respectively|where|when|while|and|the|a|an)\b",
    re.I,
)
RE_EQ_INDICATOR = re.compile(
    r"(?:[/×*+^∑√÷±]|(?:\s[-–−]\s)|\(\s*(?:μg|ug|mg|kg|g|mL|L|mol|mM|%|s|min|h|10)\b|\(\d+(?:\.\d+)?\)\s*$)",
    re.I,
)
RE_SHORT_SECTION_HEADING = re.compile(
    r"\b(?:conflict\s+of\s+interest|competing\s+interest|funding|acknowledg|ethics|data\s+availability|consent|declaration)\b",
    re.I,
)


@dataclass
class TextLine:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    y_base: float
    text: str
    size: float
    is_bold: bool
    is_italic: bool
    column: int = -1  # 0 = kiri, 1 = kanan, -1 = tengah/lebar penuh
    italic_alpha_ratio: float = 0.0
    has_math_font: bool = False

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class VisualObject:
    page: int
    kind: str  # "image", "table", "vector"
    x0: float
    y0: float
    x1: float
    y1: float
    is_full_width: bool = False

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def _span_is_bold(span: Dict[str, Any]) -> bool:
    flags = int(span.get("flags", 0))
    font_l = str(span.get("font", "")).lower()
    if flags & 16:
        return True
    return any(tok in font_l for tok in ("bold", "demi", "black", "heavy", "tibo", "hebo", "hvbo"))


def _span_is_italic(span: Dict[str, Any]) -> bool:
    flags = int(span.get("flags", 0))
    font_l = str(span.get("font", "")).lower()
    if flags & 2:
        return True
    return any(tok in font_l for tok in ("italic", "oblique", "slant", "tiit", "tibi"))


def _span_is_math_font(span: Dict[str, Any]) -> bool:
    font_l = str(span.get("font", "")).lower()
    return any(tok in font_l for tok in ("math", "symbol", "cambria", "stix", "cmmi", "cmsy"))


def _bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float], pad: float = 0.0) -> bool:
    return not (a[2] <= b[0] - pad or a[0] >= b[2] + pad or a[3] <= b[1] - pad or a[1] >= b[3] + pad)


def _merge_boxes(boxes: List[Tuple[float, float, float, float]], gap: float = 6.0) -> List[Tuple[float, float, float, float]]:
    if not boxes:
        return []
    rects = list(boxes)
    changed = True
    while changed:
        changed = False
        merged: List[Tuple[float, float, float, float]] = []
        used = [False] * len(rects)
        for i in range(len(rects)):
            if used[i]:
                continue
            cur = rects[i]
            used[i] = True
            growing = True
            while growing:
                growing = False
                for j in range(len(rects)):
                    if not used[j] and _bbox_intersects(cur, rects[j], pad=gap):
                        r = rects[j]
                        cur = (min(cur[0], r[0]), min(cur[1], r[1]), max(cur[2], r[2]), max(cur[3], r[3]))
                        used[j] = True
                        growing = True
                        changed = True
            merged.append(cur)
        rects = merged
    return rects


def _detect_body_font_size(doc: Any, fallback: float = 11.0) -> float:
    counter: Counter = Counter()
    for page in doc:
        flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
        data = page.get_text("dict", flags=flags)
        for blk in data.get("blocks", []):
            for line in blk.get("lines", []):
                for sp in line.get("spans", []):
                    txt = sp.get("text", "").strip()
                    sz = round(float(sp.get("size", 0.0)), 1)
                    if txt and 9.0 <= sz <= 13.0:
                        counter[sz] += len(txt)
    if not counter:
        return fallback
    return float(counter.most_common(1)[0][0])


def _extract_raw_spans(page: Any, page_num: int) -> List[Dict[str, Any]]:
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    data = page.get_text("dict", flags=flags)
    spans: List[Dict[str, Any]] = []
    for blk in data.get("blocks", []):
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                raw = sp.get("text", "")
                if not raw.strip():
                    continue
                bbox = sp.get("bbox", (0.0, 0.0, 0.0, 0.0))
                origin = sp.get("origin", (bbox[0], bbox[3] - 2.0))
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    continue
                spans.append(
                    {
                        "page": page_num,
                        "text": raw,
                        "x0": float(bbox[0]),
                        "y0": float(bbox[1]),
                        "x1": float(bbox[2]),
                        "y1": float(bbox[3]),
                        "y_base": float(origin[1]),
                        "size": float(sp.get("size", 0.0)),
                        "bold": _span_is_bold(sp),
                        "italic": _span_is_italic(sp),
                        "math_font": _span_is_math_font(sp),
                    }
                )
    return spans


def _assign_column(x0: float, x1: float, rules: JournalRules) -> int:
    mid = 0.5 * (x0 + x1)
    width = x1 - x0
    if width > rules.col_width + 32.0:
        return -1
    if x1 <= rules.gutter_center + 8.0 and x0 < rules.gutter_center:
        return 0
    if x0 >= rules.gutter_center - 8.0 and x1 > rules.gutter_center:
        return 1
    return 0 if mid < rules.gutter_center else 1


def _extract_text_lines(page: Any, page_num: int, rules: JournalRules) -> List[TextLine]:
    spans = _extract_raw_spans(page, page_num)
    if not spans:
        return []
    spans.sort(key=lambda s: (round(s["y_base"], 1), s["x0"]))

    rows: List[List[Dict[str, Any]]] = []
    for sp in spans:
        if not rows or abs(sp["y_base"] - rows[-1][0]["y_base"]) > 1.3:
            rows.append([sp])
        else:
            rows[-1].append(sp)

    lines: List[TextLine] = []
    for row in rows:
        row.sort(key=lambda s: s["x0"])
        groups: List[List[Dict[str, Any]]] = [[row[0]]]
        for sp in row[1:]:
            prev = groups[-1][-1]
            gap = sp["x0"] - prev["x1"]
            crosses_gutter = (
                prev["x1"] <= rules.col0_right + 10.0
                and sp["x0"] >= rules.col1_left - 10.0
                and gap >= 8.5
            )
            if crosses_gutter:
                groups.append([sp])
            else:
                groups[-1].append(sp)

        for grp in groups:
            parts: List[str] = []
            for idx, sp in enumerate(grp):
                if idx > 0 and sp["x0"] - grp[idx - 1]["x1"] > 1.2 and not parts[-1].endswith(" "):
                    parts.append(" ")
                parts.append(sp["text"])
            txt = "".join(parts).strip()
            if not txt:
                continue
            non_ws_len = [max(1, len(s["text"].strip())) for s in grp]
            tot_chars = sum(non_ws_len)
            avg_size = sum(s["size"] * w for s, w in zip(grp, non_ws_len)) / tot_chars
            all_bold = all(s["bold"] for s in grp)
            all_italic = all(s["italic"] for s in grp)
            alpha_total = sum(sum(1 for ch in s["text"] if ch.isalpha()) for s in grp)
            alpha_italic = sum(sum(1 for ch in s["text"] if ch.isalpha()) for s in grp if s["italic"])
            italic_ratio = (alpha_italic / alpha_total) if alpha_total > 0 else (1.0 if all_italic else 0.0)
            has_math = any(s["math_font"] for s in grp)
            x0 = min(s["x0"] for s in grp)
            y0 = min(s["y0"] for s in grp)
            x1 = max(s["x1"] for s in grp)
            y1 = max(s["y1"] for s in grp)
            y_base = sum(s["y_base"] * w for s, w in zip(grp, non_ws_len)) / tot_chars
            lines.append(
                TextLine(
                    page=page_num,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    y_base=y_base,
                    text=txt,
                    size=avg_size,
                    is_bold=all_bold,
                    is_italic=all_italic,
                    column=_assign_column(x0, x1, rules),
                    italic_alpha_ratio=italic_ratio,
                    has_math_font=has_math,
                )
            )
    return lines


def _detect_header_footer_rules(
    page: Any,
    page_num: int,
    rules: JournalRules,
    lines: Optional[List[TextLine]] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """Deteksi garis pemisah horizontal running header & running footer."""
    header_y: Optional[float] = None
    full_footer_ys: List[float] = []
    fallback_footer_ys: List[float] = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None or r.height >= 2.5:
            continue
        if page_num >= 2 and 65.0 <= r.y0 <= 115.0 and r.width >= rules.text_width * 0.65:
            header_y = float(r.y1) if header_y is None else max(header_y, float(r.y1))
        elif 720.0 <= r.y0 <= 795.0:
            if r.width >= rules.text_width * 0.85 and r.x0 <= rules.margin_left + 15.0 and r.x1 >= rules.margin_right - 15.0:
                full_footer_ys.append(float(r.y0))
            elif r.width >= rules.text_width * 0.65 and r.y0 >= rules.content_bottom - 5.0:
                fallback_footer_ys.append(float(r.y0))

    footer_y: Optional[float] = None
    cands = full_footer_ys or fallback_footer_ys
    if cands:
        y_bot = max(cands)
        footer_y = min((y for y in cands if y_bot - 4.0 <= y <= y_bot), default=y_bot)
        if footer_y < rules.content_bottom - 2.0 and lines is not None:
            has_footer_text = any(
                footer_y - 1.0 <= ln.y_base <= footer_y + 32.0
                and (
                    ln.size <= rules.body_font_size - 0.3
                    or RE_RUNNING_FOOTER.search(ln.text)
                    or RE_PAGE_NUM_ONLY.match(ln.text.strip())
                )
                for ln in lines
            )
            if not has_footer_text:
                footer_y = None
    return header_y, footer_y


def _detect_footnote_y(page: Any, lines: List[TextLine], body_size: float, rules: JournalRules) -> Optional[float]:
    drawings = page.get_drawings()
    candidates: List[float] = []
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        if r.height < 1.5 and 40.0 <= r.width <= 170.0 and 420.0 < r.y0 < rules.content_bottom:
            if abs(r.x0 - rules.margin_left) <= 18.0 or abs(r.x0 - rules.col1_left) <= 18.0:
                candidates.append(float(r.y0))
    for y_line in sorted(candidates):
        has_small = any(
            y_line < ln.y_base < y_line + 70.0 and ln.size <= body_size - 1.2
            for ln in lines
        )
        if has_small:
            return y_line
    return None


def _detect_page1_body_top(page: Any, lines: List[TextLine], body_size: float, rules: JournalRules) -> float:
    """Cari batas atas area narasi 2-kolom di Halaman 1 setelah judul & abstrak."""
    cite_y = 0.0
    for ln in lines:
        if RE_CITE_OR_HDR.match(ln.text) and ln.y1 < 680.0:
            cite_y = max(cite_y, ln.y1)
    if cite_y > 0:
        for d in page.get_drawings():
            r = d.get("rect")
            if r and r.height < 2.5 and r.width >= rules.text_width * 0.65 and cite_y - 2.0 <= r.y0 <= cite_y + 35.0:
                cite_y = max(cite_y, float(r.y1) + 2.0)

    two_col_body = [
        ln for ln in lines
        if ln.column in (0, 1)
        and abs(ln.size - body_size) <= 0.9
        and ln.y0 >= max(rules.margin_top - 4.0, cite_y)
        and ln.y1 <= rules.content_bottom + 4.0
        and not RE_CITE_OR_HDR.match(ln.text)
    ]
    if cite_y > 0:
        if two_col_body:
            first_y = min(ln.y0 for ln in two_col_body)
            return max(rules.margin_top, min(first_y - 2.0, cite_y + 24.0))
        return max(rules.margin_top, cite_y)

    first_col_y = min((ln.y0 for ln in two_col_body), default=rules.margin_top)
    full_hdr_lines = [
        ln for ln in lines
        if ln.column == -1 and rules.margin_top - 10.0 <= ln.y0 < first_col_y and ln.y1 < 420.0
    ]
    if full_hdr_lines:
        return max(rules.margin_top, max(ln.y1 for ln in full_hdr_lines) + 2.0)
    return rules.margin_top


def _extract_visual_objects(
    page: Any,
    page_num: int,
    body_top: float,
    footnote_y: Optional[float],
    footer_rule_y: Optional[float],
    lines: List[TextLine],
    rules: JournalRules,
) -> List[VisualObject]:
    raw_items: List[Tuple[str, Tuple[float, float, float, float]]] = []
    footer_limit = footer_rule_y - 1.5 if footer_rule_y is not None else rules.content_bottom + 8.0

    # 1. Gambar raster
    try:
        imgs = page.get_image_info(xrefs=True)
    except Exception:
        imgs = []
    for im in imgs:
        bbox = im.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = (float(v) for v in bbox)
        w, h = x1 - x0, y1 - y0
        if w < 12.0 or h < 12.0:
            continue
        if w > rules.page_width - 25.0 and h > rules.page_height - 40.0:
            continue
        if page_num == 1 and y1 < rules.margin_top - 2.0:
            continue
        if y0 > footer_limit:
            continue
        raw_items.append(("image", (x0, y0, x1, y1)))

    # 2. Tabel terdeteksi
    table_boxes: List[Tuple[float, float, float, float]] = []
    try:
        finder = page.find_tables()
        tabs = getattr(finder, "tables", []) or []
        for tb in tabs:
            bbox = getattr(tb, "bbox", None)
            if not bbox:
                continue
            x0, y0, x1, y1 = (float(v) for v in bbox)
            if (x1 - x0) >= 35.0 and (y1 - y0) >= 18.0:
                if page_num == 1 and y1 < body_top - 2.0:
                    continue
                if y0 > footer_limit:
                    continue
                box = (x0, y0, x1, y1)
                table_boxes.append(box)
                raw_items.append(("table", box))
    except Exception:
        pass

    # 3. Objek vektor / bingkai tabel / grafik dari drawings
    vec_rects: List[Tuple[float, float, float, float]] = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
        w, h = x1 - x0, y1 - y0
        if w > rules.page_width - 25.0 and h > rules.page_height - 40.0:
            continue
        if y1 < (rules.margin_top - 4.0 if page_num >= 2 else body_top - 4.0):
            continue
        if y0 > footer_limit:
            continue
        if footnote_y is not None and abs(y0 - footnote_y) <= 2.5 and h < 2.0:
            continue
        if footer_rule_y is not None and abs(y0 - footer_rule_y) <= 2.5 and h < 2.5:
            continue
        if h < 1.8 and w >= rules.text_width * 0.65 and (y0 > rules.content_bottom - 18.0 or y0 < rules.margin_top + 12.0):
            continue
        if any(_bbox_intersects((x0, y0, x1, y1), tb, pad=3.0) for tb in table_boxes):
            continue
        if w < 3.0 and h < 3.0:
            continue
        vec_rects.append((x0, y0, x1, y1))

    for vbox in _merge_boxes(vec_rects, gap=8.0):
        vw, vh = vbox[2] - vbox[0], vbox[3] - vbox[1]
        if vh < 14.0 and vw < 24.0:
            continue
        if vh < 3.0:
            continue
        raw_items.append(("vector", vbox))

    merged_items: List[VisualObject] = []
    all_boxes = [item[1] for item in raw_items]
    for mbox in _merge_boxes(all_boxes, gap=6.0):
        overlapping_kinds = [k for k, b in raw_items if _bbox_intersects(mbox, b, pad=2.0)]
        kind = "table" if "table" in overlapping_kinds else ("image" if "image" in overlapping_kinds else "vector")
        overlapping_side_text = any(
            (ln.y1 > mbox[1] + 2.0 and ln.y0 < mbox[3] - 2.0)
            and (ln.x1 <= mbox[0] - 4.0 or ln.x0 >= mbox[2] + 4.0)
            and ln.y0 >= body_top - 2.0
            for ln in lines
        )
        w = mbox[2] - mbox[0]
        center_offset = abs(0.5 * (mbox[0] + mbox[2]) - rules.gutter_center)
        centered = center_offset <= 28.0
        strictly_centered = center_offset <= 12.0
        is_full = (
            not overlapping_side_text
            and (
                (w > rules.col_width + 55.0 and (centered or w >= 300.0))
                or (w > rules.col_width + rules.gutter_tol and strictly_centered)
            )
        )
        merged_items.append(
            VisualObject(
                page=page_num,
                kind=kind,
                x0=mbox[0],
                y0=mbox[1],
                x1=mbox[2],
                y1=mbox[3],
                is_full_width=is_full,
            )
        )
    return merged_items


def _is_caption_line(ln: TextLine, objects: List[VisualObject], body_size: float) -> bool:
    if RE_CAPTION.match(ln.text) and (ln.is_bold or ln.size <= body_size - 0.3):
        return True
    if ln.size <= body_size - 1.2:
        for obj in objects:
            if abs(ln.y0 - obj.y1) <= 28.0 or abs(obj.y0 - ln.y1) <= 28.0:
                if _bbox_intersects((ln.x0, ln.y0 - 28.0, ln.x1, ln.y1 + 28.0), obj.bbox):
                    return True
    return False


def _is_header_or_footer_line(
    ln: TextLine,
    page_num: int,
    body_top: float,
    header_rule_y: Optional[float],
    footer_rule_y: Optional[float],
    rules: JournalRules,
) -> bool:
    txt = ln.text.strip()
    if footer_rule_y is not None and ln.y0 >= footer_rule_y - 1.5:
        return True
    if ln.y0 >= rules.content_bottom + 1.5:
        return True
    if ln.y0 >= 735.0 and RE_RUNNING_FOOTER.search(txt):
        return True
    if ln.y0 >= 752.0 and RE_PAGE_NUM_ONLY.match(txt):
        return True
    if page_num >= 2:
        if header_rule_y is not None and ln.y1 <= header_rule_y + 1.5:
            return True
        if ln.y1 <= rules.margin_top - 1.5 and ln.size <= rules.body_font_size - 0.3:
            return True
    if page_num == 1 and ln.y1 < body_top - 2.0:
        return True
    return False


def _is_heading_line(ln: TextLine, body_size: float) -> bool:
    txt = ln.text.strip()
    if len(txt) < 3 or RE_PAGE_NUM_ONLY.match(txt):
        return False
    if RE_CAPTION.match(txt):
        return False
    return ln.is_bold and ln.size >= body_size - 0.6


def _is_equation_line(ln: TextLine, body_size: float) -> bool:
    """Deteksi baris rumus display berdiri sendiri (mis. 'AAI = ... / ...' atau 'y = ax + b (1)')."""
    txt = ln.text.strip()
    if len(txt) < 5 or _is_heading_line(ln, body_size) or RE_CAPTION.match(txt) or RE_URL_META.search(txt):
        return False
    if abs(ln.size - body_size) > 2.2:
        return False

    core = re.sub(r"\s*\(\d+(?:\.\d+)?\)\s*$", "", txt).strip()
    # Buang isi tanda kurung untuk mencari '=' tingkat terluar
    top_level = core
    while True:
        reduced = re.sub(r"\([^()]*\)", " ", top_level)
        if reduced == top_level:
            break
        top_level = reduced

    m_eq = re.search(r"[=≈≤≥]", top_level)
    if not m_eq:
        return bool(ln.has_math_font and any(op in core for op in ("=", "≈", "≤", "≥", "∑", "∫", "√", "÷")))

    lhs = top_level[:m_eq.start()].strip()
    rhs = top_level[m_eq.end():].strip()
    if not lhs or not rhs or len(lhs) > 28:
        return False
    lhs_words = lhs.split()
    if not (1 <= len(lhs_words) <= 4):
        return False
    if lhs.endswith((",", ";", ".", ":")):
        return False
    if len(lhs_words) >= 2 and RE_PROSE_LHS_STOP.search(lhs):
        return False
    if ln.has_math_font or RE_EQ_INDICATOR.search(txt):
        return True
    if len(lhs_words) <= 2 and re.search(r"[0-9A-Za-z]\s*[/×*+\-^]\s*[0-9A-Za-z(]", rhs):
        return True
    return False


def _is_para_start(
    ln: TextLine,
    prev: Optional[TextLine],
    leading: float,
    rules: JournalRules,
) -> bool:
    if prev is None:
        return False
    col_left = rules.col0_left if ln.column == 0 else rules.col1_left
    prev_col_right = rules.col0_right if prev.column == 0 else rules.col1_right
    if _is_heading_line(prev, rules.body_font_size) or _is_equation_line(prev, rules.body_font_size):
        return True
    if ln.x0 - col_left >= 6.0 and (ln.x1 - ln.x0) > 20.0:
        return True
    if prev.page == ln.page and prev.column == ln.column and (ln.y_base - prev.y_base) > leading * 1.45:
        return True
    if (prev_col_right - prev.x1) >= 28.0 and prev.text.rstrip().endswith((".", "!", "?", '"', "”", ")", "]")):
        return True
    if (prev_col_right - prev.x1) >= 42.0:
        return True
    return False


def _ends_paragraph(ln: TextLine, next_ln: Optional[TextLine], leading: float, rules: JournalRules) -> bool:
    col_right = rules.col0_right if ln.column == 0 else rules.col1_right
    if next_ln is not None:
        if _is_heading_line(next_ln, rules.body_font_size) or _is_equation_line(next_ln, rules.body_font_size):
            return True
        if _is_para_start(next_ln, ln, leading, rules):
            return True
    shortness = col_right - ln.x1
    has_terminal_punct = ln.text.rstrip().endswith((".", "!", "?", '"', "”", ")", "]"))
    if has_terminal_punct and shortness >= 18.0:
        return True
    return shortness >= 36.0


def _detect_citation_to_intro_gap(
    page: Any,
    lines: List[TextLine],
    body_size: float,
    rules: JournalRules,
) -> Optional[Issue]:
    """Cek jarak antara blok 'To cite this article' dengan judul sub-bab pertama ('INTRODUCTION')."""
    cite_lines = [ln for ln in lines if RE_CITE_OR_HDR.match(ln.text) and "to cite" in ln.text.lower() or (
        "open access article" in ln.text.lower() and ln.y0 < 680.0
    )]
    if not cite_lines:
        return None
    last_cite_y1 = max(ln.y1 for ln in cite_lines)
    cite_bottom_y = last_cite_y1
    for d in page.get_drawings():
        r = d.get("rect")
        if r and r.height < 2.5 and r.width >= rules.text_width * 0.65 and last_cite_y1 - 2.0 <= r.y0 <= last_cite_y1 + 20.0:
            cite_bottom_y = max(cite_bottom_y, float(r.y1))

    # Cari sub-bab INTRODUCTION (atau heading tingkat-1 pertama tepat sesudah blok sitasi)
    candidates = [
        ln for ln in lines
        if ln.y0 >= cite_bottom_y - 2.0 and ln.y0 <= cite_bottom_y + 150.0 and ln.column in (0, -1)
    ]
    candidates.sort(key=lambda ln: ln.y0)
    intro_ln = next(
        (ln for ln in candidates if re.search(r"\bINTRODUCTION\b", ln.text, re.I)),
        None,
    )
    if intro_ln is None:
        intro_ln = next(
            (ln for ln in candidates if _is_heading_line(ln, body_size)),
            None,
        )
    if intro_ln is None:
        return None

    gap = intro_ln.y0 - cite_bottom_y
    if abs(gap - rules.cite_to_intro_pt) > rules.cite_to_intro_tol:
        return Issue(
            page=1,
            category="jarak_sitasi",
            severity="error",
            title=f"Jarak blok sitasi ke judul pertama {gap:.1f} pt (wajib {rules.cite_to_intro_pt:.0f} pt)",
            message=(
                f"Jarak antara penutup blok 'To cite this article' (y={cite_bottom_y:.1f} pt) "
                f"dengan \"{intro_ln.text[:35]}\" (y={intro_ln.y0:.1f} pt) adalah {gap:.1f} pt, "
                f"seharusnya {rules.cite_to_intro_pt:.0f} pt (2 baris)."
            ),
            suggestion="Atur jarak di bawah garis 'To cite this article' ke judul sub-bab pertama menjadi tepat 22 pt (2 baris).",
            bbox=(rules.margin_left, min(cite_bottom_y, intro_ln.y0), rules.col0_right, max(cite_bottom_y + 2.0, intro_ln.y0)),
            column=0,
        )
    return None


def _object_with_caption_bottom(obj: VisualObject, lines: List[TextLine], body_size: float) -> float:
    end_y = obj.y1
    for ln in lines:
        if obj.y1 - 2.0 <= ln.y0 <= obj.y1 + 28.0:
            if not (ln.x1 < obj.x0 - 8.0 or ln.x0 > obj.x1 + 8.0):
                if _is_caption_line(ln, [obj], body_size):
                    end_y = max(end_y, ln.y1)
    return end_y


def _check_equations_in_segment(
    seg_lns: List[TextLine],
    page_num: int,
    col: int,
    body_size: float,
    rules: JournalRules,
) -> Tuple[List[Issue], List[int]]:
    """Periksa rumus (italic, no-indent, spasi 11 pt atas/bawah) dan kembalikan indeks baris rumus."""
    eq_indices = [idx for idx, ln in enumerate(seg_lns) if _is_equation_line(ln, body_size)]
    if not eq_indices:
        return [], []

    eq_set = set(eq_indices)
    # Kelompokkan baris rumus berurutan menjadi satu blok persamaan
    blocks: List[List[int]] = []
    for idx in eq_indices:
        if blocks and idx == blocks[-1][-1] + 1 and (seg_lns[idx].y_base - seg_lns[idx - 1].y_base) <= rules.grid_pt * 1.65:
            blocks[-1].append(idx)
        else:
            blocks.append([idx])

    issues: List[Issue] = []
    col_left = rules.col0_left if col == 0 else rules.col1_left
    for blk in blocks:
        blk_lns = [seg_lns[i] for i in blk]
        top_ln, bot_ln = blk_lns[0], blk_lns[-1]
        bbox = (
            min(l.x0 for l in blk_lns),
            min(l.y0 for l in blk_lns),
            max(l.x1 for l in blk_lns),
            max(l.y1 for l in blk_lns),
        )
        preview = top_ln.text[:55]

        # 1. Wajib Italic (kecuali murni simbol math font tanpa huruf biasa non-italic)
        all_math = all(l.has_math_font for l in blk_lns)
        if not all_math:
            avg_italic = sum(l.italic_alpha_ratio for l in blk_lns) / len(blk_lns)
            if avg_italic < 0.70:
                issues.append(
                    Issue(
                        page=page_num,
                        category="rumus",
                        severity="error",
                        title="Rumus tidak ditulis miring (italic)",
                        message=f"Rumus \"{preview}\" tidak berformat italic (rasio huruf miring {avg_italic * 100:.0f}%).",
                        suggestion="Terapkan gaya huruf miring (Italic) pada seluruh teks variabel/persamaan rumus.",
                        bbox=bbox,
                        column=col,
                    )
                )

        # 2. Wajib No-Indent (rata tepi kiri kolom)
        min_x0 = min(l.x0 for l in blk_lns)
        if min_x0 - col_left > rules.eq_indent_tol:
            issues.append(
                Issue(
                    page=page_num,
                    category="rumus",
                    severity="error",
                    title="Rumus menjorok ke dalam (seharusnya no-indent)",
                    message=f"Rumus \"{preview}\" menjorok {min_x0 - col_left:.1f} pt dari tepi kiri kolom ({col_left:.1f} pt).",
                    suggestion="Hilangkan first-line indent pada paragraf rumus (rata kiri kolom, indent = 0 pt).",
                    bbox=bbox,
                    column=col,
                )
            )

        # 3. Spasi >= 11 pt di atas & di bawah rumus terhadap teks tetangga
        first_i, last_i = blk[0], blk[-1]
        if first_i > 0 and (first_i - 1) not in eq_set:
            prev_ln = seg_lns[first_i - 1]
            dy_base = top_ln.y_base - prev_ln.y_base
            dy_box = top_ln.y0 - prev_ln.y1
            if dy_base < rules.eq_space_min_base and dy_box < rules.eq_space_min_box:
                issues.append(
                    Issue(
                        page=page_num,
                        category="rumus",
                        severity="error",
                        title="Jarak spasi di atas rumus kurang dari 11 pt",
                        message=(
                            f"Jarak baseline dari teks di atasnya ke rumus \"{preview}\" hanya "
                            f"{dy_base:.1f} pt (butuh baris kosong 11 pt / jarak baseline >= 22 pt)."
                        ),
                        suggestion="Tambahkan spasi 11 pt (1 baris kosong) sebelum blok rumus.",
                        bbox=(bbox[0], prev_ln.y1, bbox[2], bbox[3]),
                        column=col,
                    )
                )
        if last_i + 1 < len(seg_lns) and (last_i + 1) not in eq_set:
            next_ln = seg_lns[last_i + 1]
            dy_base = next_ln.y_base - bot_ln.y_base
            dy_box = next_ln.y0 - bot_ln.y1
            if dy_base < rules.eq_space_min_base and dy_box < rules.eq_space_min_box:
                issues.append(
                    Issue(
                        page=page_num,
                        category="rumus",
                        severity="error",
                        title="Jarak spasi di bawah rumus kurang dari 11 pt",
                        message=(
                            f"Jarak baseline dari rumus \"{preview}\" ke teks di bawahnya hanya "
                            f"{dy_base:.1f} pt (butuh baris kosong 11 pt / jarak baseline >= 22 pt)."
                        ),
                        suggestion="Tambahkan spasi 11 pt (1 baris kosong) sesudah blok rumus.",
                        bbox=(bbox[0], bbox[1], bbox[2], next_ln.y0),
                        column=col,
                    )
                )

    return issues, eq_indices


def analyze_pdf(pdf_path: str, rules: Optional[JournalRules] = None) -> QAReport:
    """Analisis berkas PDF eksternal terhadap aturan tata letak jurnal."""
    if rules is None:
        rules = JournalRules()
    abs_path = os.path.abspath(pdf_path)
    doc = fitz.open(abs_path)
    try:
        page_count = doc.page_count
        body_size = _detect_body_font_size(doc, fallback=rules.body_font_size)
        issues: List[Issue] = []
        page_metrics: List[Dict[str, Any]] = []
        all_segments: List[Dict[str, Any]] = []

        per_page_data: List[Dict[str, Any]] = []
        for idx in range(page_count):
            page_num = idx + 1
            page = doc[idx]
            lines = _extract_text_lines(page, page_num, rules)
            header_rule_y, footer_rule_y = _detect_header_footer_rules(page, page_num, rules, lines=lines)
            footnote_y = _detect_footnote_y(page, lines, body_size, rules)
            body_top = _detect_page1_body_top(page, lines, body_size, rules) if page_num == 1 else rules.margin_top
            objects = _extract_visual_objects(page, page_num, body_top, footnote_y, footer_rule_y, lines, rules)
            per_page_data.append(
                {
                    "page": page_num,
                    "page_obj": page,
                    "lines": lines,
                    "header_rule_y": header_rule_y,
                    "footer_rule_y": footer_rule_y,
                    "footnote_y": footnote_y,
                    "body_top": body_top,
                    "objects": objects,
                    "narrative_lines": [],
                }
            )

        # Jarak blok "To cite this article" -> "INTRODUCTION" di Halaman 1
        if per_page_data and rules.check_cite_gap:
            cite_issue = _detect_citation_to_intro_gap(
                per_page_data[0]["page_obj"],
                per_page_data[0]["lines"],
                body_size,
                rules,
            )
            if cite_issue is not None:
                issues.append(cite_issue)

        # Referensi baseline bawah halaman penuh
        non_last_max_y = []
        for pd in per_page_data[:-1]:
            f_lim = pd["footer_rule_y"] - 1.5 if pd["footer_rule_y"] is not None else rules.content_bottom + 1.0
            if pd["footnote_y"] is not None:
                f_lim = min(f_lim, pd["footnote_y"] - 8.0)
            vals = [
                ln.y_base for ln in pd["lines"]
                if not _is_header_or_footer_line(ln, pd["page"], pd["body_top"], pd["header_rule_y"], pd["footer_rule_y"], rules)
                and abs(ln.size - body_size) <= 0.9
                and ln.y0 >= pd["body_top"] - 2.0
                and ln.y_base <= f_lim
            ]
            non_last_max_y.append(max(vals, default=0.0))

        ref_bottom_base = (
            max(non_last_max_y)
            if non_last_max_y and max(non_last_max_y) >= rules.content_bottom - 20.0
            else rules.content_bottom - 2.69
        )

        for pd in per_page_data:
            page_num = pd["page"]
            lines: List[TextLine] = pd["lines"]
            header_rule_y: Optional[float] = pd["header_rule_y"]
            footer_rule_y: Optional[float] = pd["footer_rule_y"]
            footnote_y: Optional[float] = pd["footnote_y"]
            body_top: float = pd["body_top"]
            objects: List[VisualObject] = pd["objects"]
            eff_top = rules.margin_top if page_num >= 2 else body_top
            eff_bottom = min(rules.content_bottom, footer_rule_y - 1.5) if footer_rule_y is not None else rules.content_bottom
            if footnote_y is not None:
                eff_bottom = min(eff_bottom, footnote_y - 8.0)

            # 1. Cek Objek Visual (ketat: margin_tol & gutter_tol)
            for obj in objects:
                k_name = {"image": "Gambar", "table": "Tabel", "vector": "Grafik/Kotak"}.get(obj.kind, "Objek")
                if obj.x0 < rules.margin_left - rules.margin_tol:
                    issues.append(
                        Issue(
                            page=page_num,
                            category="margin_objek",
                            severity="error",
                            title=f"{k_name} keluar margin kiri",
                            message=f"{k_name} dimulai di x={obj.x0:.1f} pt, melewati batas margin kiri {rules.margin_left:.1f} pt.",
                            suggestion="Geser atau perkecil lebar objek agar sisi kiri berada >= 76.5 pt.",
                            bbox=obj.bbox,
                        )
                    )
                if obj.x1 > rules.margin_right + rules.margin_tol:
                    issues.append(
                        Issue(
                            page=page_num,
                            category="margin_objek",
                            severity="error",
                            title=f"{k_name} keluar margin kanan",
                            message=f"{k_name} berakhir di x={obj.x1:.1f} pt, melewati batas margin kanan {rules.margin_right:.1f} pt.",
                            suggestion="Geser atau perkecil lebar objek agar sisi kanan berada <= 518.7 pt.",
                            bbox=obj.bbox,
                        )
                    )
                if page_num >= 2 and obj.y0 < rules.margin_top - rules.margin_tol:
                    issues.append(
                        Issue(
                            page=page_num,
                            category="margin_objek",
                            severity="error",
                            title=f"{k_name} menabrak margin atas",
                            message=f"{k_name} mulai di y={obj.y0:.1f} pt (< batas atas {rules.margin_top:.1f} pt).",
                            suggestion="Turunkan objek ke y >= 102.4 pt agar tidak menabrak area header.",
                            bbox=obj.bbox,
                        )
                    )
                if obj.y1 > eff_bottom + rules.margin_tol:
                    issues.append(
                        Issue(
                            page=page_num,
                            category="margin_objek",
                            severity="error",
                            title=f"{k_name} melampaui batas bawah/footer",
                            message=f"{k_name} mencapai y={obj.y1:.1f} pt (> batas bawah {eff_bottom:.1f} pt).",
                            suggestion="Kecilkan tinggi objek atau pindahkan ke halaman berikutnya.",
                            bbox=obj.bbox,
                        )
                    )
                if not obj.is_full_width:
                    crosses_from_left = obj.x0 < rules.col0_right - 10.0 and obj.x1 > rules.col0_right + rules.gutter_tol
                    crosses_from_right = obj.x1 > rules.col1_left + 10.0 and obj.x0 < rules.col1_left - rules.gutter_tol
                    if crosses_from_left or crosses_from_right:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="margin_objek",
                                severity="error",
                                title=f"{k_name} melintasi batas kolom / gutter tengah",
                                message=(
                                    f"{k_name} ({obj.x0:.1f}..{obj.x1:.1f} pt) melewati batas kolom "
                                    f"({rules.col0_right:.1f}–{rules.col1_left:.1f} pt) tanpa menjadi objek penuh 2 kolom simetris."
                                ),
                                suggestion=(
                                    "Sesuaikan lebar ke 1 kolom (<= 213.6 pt) atau jadikan span 2 kolom "
                                    "simetris di tengah halaman (lebar <= 442.2 pt)."
                                ),
                                bbox=obj.bbox,
                            )
                        )

            # 2. Saring baris narasi (running header/footer dikecualikan) & cek toleransi margin teks
            narrative_lines: List[TextLine] = []
            for ln in lines:
                if _is_header_or_footer_line(ln, page_num, body_top, header_rule_y, footer_rule_y, rules):
                    continue
                if any(_bbox_intersects(ln.bbox, obj.bbox, pad=1.5) for obj in objects):
                    continue
                if footnote_y is not None and ln.y_base > footnote_y - 2.0:
                    continue
                if _is_caption_line(ln, objects, body_size):
                    continue

                if ln.y0 >= eff_top - 4.0 and abs(ln.size - body_size) <= 1.2:
                    if ln.x0 < rules.margin_left - rules.text_margin_tol:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="margin_teks",
                                severity="error",
                                title="Teks keluar margin kiri",
                                message=f"Baris \"{ln.text[:45]}\" dimulai di x={ln.x0:.1f} pt (< {rules.margin_left - rules.text_margin_tol:.1f} pt).",
                                suggestion="Periksa frame teks atau indentasi negatif di Scribus/PageMaker.",
                                bbox=ln.bbox,
                                column=ln.column if ln.column in (0, 1) else None,
                            )
                        )
                    if ln.x1 > rules.margin_right + rules.text_margin_tol:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="margin_teks",
                                severity="error",
                                title="Teks keluar margin kanan",
                                message=f"Baris \"{ln.text[:45]}\" berakhir di x={ln.x1:.1f} pt (> {rules.margin_right + rules.text_margin_tol:.1f} pt).",
                                suggestion="Periksa lebar text frame atau overflow kata tanpa wrap.",
                                bbox=ln.bbox,
                                column=ln.column if ln.column in (0, 1) else None,
                            )
                        )
                    if ln.y1 > eff_bottom + 2.5:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="margin_teks",
                                severity="fatal",
                                title="Teks melewati dasar halaman / menabrak footer",
                                message=f"Baris \"{ln.text[:45]}\" mencapai y={ln.y1:.1f} pt (> {eff_bottom:.1f} pt).",
                                suggestion="Kurangi baris pada kolom ini atau pindahkan sisa baris ke kolom/halaman berikutnya.",
                                bbox=ln.bbox,
                                column=ln.column if ln.column in (0, 1) else None,
                            )
                        )
                    if ln.column == 0 and ln.x1 > rules.col0_right + rules.text_gutter_tol:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="margin_teks",
                                severity="error",
                                title="Teks kolom kiri masuk ke celah kolom (gutter)",
                                message=f"Baris \"{ln.text[:45]}\" melampaui batas kanan kolom kiri ({rules.col0_right:.1f} pt -> {ln.x1:.1f} pt).",
                                suggestion="Perbaiki lebar frame kolom kiri menjadi 213.6 pt.",
                                bbox=ln.bbox,
                                column=0,
                            )
                        )
                    if ln.column == 1 and ln.x0 < rules.col1_left - rules.text_gutter_tol:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="margin_teks",
                                severity="error",
                                title="Teks kolom kanan masuk ke celah kolom (gutter)",
                                message=f"Baris \"{ln.text[:45]}\" dimulai sebelum batas kiri kolom kanan ({ln.x0:.1f} pt < {rules.col1_left:.1f} pt).",
                                suggestion="Perbaiki posisi x frame kolom kanan menjadi 305.1 pt.",
                                bbox=ln.bbox,
                                column=1,
                            )
                        )

                if ln.column in (0, 1) and abs(ln.size - body_size) <= 1.2 and ln.y0 >= eff_top - 3.0:
                    narrative_lines.append(ln)

            pd["narrative_lines"] = narrative_lines

            # 3. Bagi halaman menjadi segmen horizontal berdasarkan objek selebar halaman
            full_objs = sorted([o for o in objects if o.is_full_width], key=lambda o: o.y0)
            y_splits = [eff_top - 2.0]
            obj_bands: List[Tuple[float, float]] = []
            for fo in full_objs:
                if fo.y0 > y_splits[-1]:
                    obj_bands.append((y_splits[-1], fo.y0))
                y_splits.append(fo.y1)
            obj_bands.append((y_splits[-1], eff_bottom + 2.0))

            # Cek pita teks terjepit (< 3 baris) di antara dua objek selebar halaman
            for seg_i in range(len(full_objs) - 1):
                y_a = full_objs[seg_i].y1
                y_b = full_objs[seg_i + 1].y0
                trapped = [ln for ln in narrative_lines if y_a - 1.0 < ln.y_base < y_b + 1.0]
                n_trapped = max(
                    sum(1 for ln in trapped if ln.column == 0),
                    sum(1 for ln in trapped if ln.column == 1),
                )
                if 0 < n_trapped < rules.min_ribbon_lines:
                    issues.append(
                        Issue(
                            page=page_num,
                            category="pita_teks",
                            severity="error",
                            title="Pita teks terjepit di antara dua objek",
                            message=f"Hanya ada {n_trapped} baris teks terjepit di antara dua objek lebar penuh (min. {rules.min_ribbon_lines} baris).",
                            suggestion="Rapatkan kedua objek atau pindahkan teks tersebut ke bawah objek kedua.",
                            bbox=(rules.margin_left, y_a, rules.margin_right, y_b),
                        )
                    )

            # Cek pita teks terjepit (< 3 baris) di antara dua objek besar dalam kolom yang sama
            for col in (0, 1):
                col_objs = sorted(
                    [
                        o for o in objects
                        if not o.is_full_width and o.height >= 25.0
                        and ((col == 0 and o.x0 < rules.gutter_center) or (col == 1 and o.x1 > rules.gutter_center))
                    ],
                    key=lambda o: o.y0,
                )
                for oi in range(len(col_objs) - 1):
                    ya = _object_with_caption_bottom(col_objs[oi], lines, body_size)
                    yb = col_objs[oi + 1].y0
                    if yb <= ya:
                        continue
                    # Abaikan bila dipotong oleh objek selebar halaman di antaranya
                    if any(ya < fo.y0 < yb for fo in full_objs):
                        continue
                    trapped_c = [ln for ln in narrative_lines if ln.column == col and ya - 1.0 < ln.y_base < yb + 1.0]
                    if 0 < len(trapped_c) < rules.min_ribbon_lines:
                        c_left = rules.col0_left if col == 0 else rules.col1_left
                        c_right = rules.col0_right if col == 0 else rules.col1_right
                        issues.append(
                            Issue(
                                page=page_num,
                                category="pita_teks",
                                severity="error",
                                title="Teks pendek terjepit di antara dua objek kolom",
                                message=(
                                    f"Hanya ada {len(trapped_c)} baris teks terjepit di antara dua objek pada "
                                    f"kolom {'kiri' if col == 0 else 'kanan'} (min. {rules.min_ribbon_lines} baris)."
                                ),
                                suggestion="Pindahkan teks pendek ke luar celah antar objek atau rapatkan objek.",
                                bbox=(c_left, ya, c_right, yb),
                                column=col,
                            )
                        )

            # 4. Periksa Rumus, Keseimbangan Kolom, Ruang Kosong, & Orphan Heading per segmen
            page_col0_lines = [ln for ln in narrative_lines if ln.column == 0]
            page_col1_lines = [ln for ln in narrative_lines if ln.column == 1]
            equation_lines_all: List[TextLine] = []

            for seg_idx, (seg_top, seg_bot) in enumerate(obj_bands):
                is_bottom_seg = seg_idx == len(obj_bands) - 1
                seg_cols: Dict[int, List[TextLine]] = {}
                eq_idx_by_col: Dict[int, set] = {0: set(), 1: set()}
                for col in (0, 1):
                    col_lns = sorted(
                        [ln for ln in narrative_lines if ln.column == col and seg_top <= ln.y_base <= seg_bot],
                        key=lambda ln: ln.y_base,
                    )
                    seg_cols[col] = col_lns
                    if col_lns and rules.check_equations:
                        eq_issues, eq_idxs = _check_equations_in_segment(col_lns, page_num, col, body_size, rules)
                        issues.extend(eq_issues)
                        eq_idx_by_col[col] = set(eq_idxs)
                        equation_lines_all.extend(col_lns[idx_e] for idx_e in eq_idxs)
                    if col_lns:
                        all_segments.append(
                            {
                                "page": page_num,
                                "seg_idx": seg_idx,
                                "col": col,
                                "lines": col_lns,
                                "eq_set": eq_idx_by_col[col],
                            }
                        )

                l0 = seg_cols[0]
                l1 = seg_cols[1]
                objs_col0 = sorted(
                    [o for o in objects if not o.is_full_width and o.width >= 30.0 and o.height >= 25.0
                     and o.x0 < rules.gutter_center and seg_top - 4.0 <= o.y0 <= seg_bot],
                    key=lambda o: o.y0,
                )
                objs_col1 = sorted(
                    [o for o in objects if not o.is_full_width and o.width >= 30.0 and o.height >= 25.0
                     and o.x1 > rules.gutter_center and seg_top - 4.0 <= o.y0 <= seg_bot],
                    key=lambda o: o.y0,
                )
                col0_closed_by_obj = any(
                    _object_with_caption_bottom(o, lines, body_size) >= eff_bottom - 30.0 for o in objs_col0
                )
                col1_closed_by_obj = any(
                    _object_with_caption_bottom(o, lines, body_size) >= eff_bottom - 30.0 for o in objs_col1
                )

                # Aturan mutlak: Kolom kanan <= Kolom kiri pada setiap segmen (toleransi jitter minor)
                if l0 and l1 and not col0_closed_by_obj and not col1_closed_by_obj:
                    max_y0 = max(ln.y_base for ln in l0)
                    max_y1 = max(ln.y_base for ln in l1)
                    if max_y1 > max_y0 + rules.right_gt_left_tol:
                        diff = max_y1 - max_y0
                        issues.append(
                            Issue(
                                page=page_num,
                                category="kolom",
                                severity="fatal",
                                title="Kolom kanan lebih panjang dari kolom kiri",
                                message=(
                                    f"Baseline terakhir kolom kanan ({max_y1:.1f} pt) lebih rendah {diff:.1f} pt "
                                    f"daripada kolom kiri ({max_y0:.1f} pt)."
                                ),
                                suggestion="Seimbangkan panjang teks antar kolom agar kolom kiri >= kolom kanan.",
                                bbox=(rules.col1_left, max_y0, rules.col1_right, max_y1 + 4.0),
                                column=1,
                            )
                        )

                # Kolom penuh objek vs kolom pasangan kosong melompong
                seg_open_bot = min(seg_bot, eff_bottom)
                seg_open_top = max(seg_top, eff_top)
                seg_open_h = seg_open_bot - seg_open_top
                if seg_open_h >= rules.empty_col_min_height:
                    if objs_col0 and not l1 and not objs_col1 and sum(o.height for o in objs_col0) >= 100.0:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="kolom",
                                severity="fatal",
                                title="Kolom kanan kosong melompong di samping kolom berisi objek",
                                message="Kolom kiri berisi objek besar namun kolom kanan pada segmen ini kosong sama sekali.",
                                suggestion="Alirkan teks ke kolom kanan atau ubah objek besar menjadi format 2 kolom penuh.",
                                bbox=(rules.col1_left, seg_open_top, rules.col1_right, seg_open_bot),
                                column=1,
                            )
                        )
                    elif objs_col1 and not l0 and not objs_col0 and sum(o.height for o in objs_col1) >= 100.0:
                        issues.append(
                            Issue(
                                page=page_num,
                                category="kolom",
                                severity="fatal",
                                title="Kolom kiri kosong melompong di samping kolom berisi objek",
                                message="Kolom kanan berisi objek namun kolom kiri kosong pada segmen yang sama.",
                                suggestion="Isi kolom kiri terlebih dahulu atau gunakan layout objek 2 kolom penuh.",
                                bbox=(rules.col0_left, seg_open_top, rules.col0_right, seg_open_bot),
                                column=0,
                            )
                        )

                # Ruang kosong di bawah gambar/objek kolom pada halaman non-akhir
                if page_num < page_count:
                    barrier_y = min(seg_bot, eff_bottom)
                    for col, col_objs, col_lns in ((0, objs_col0, l0), (1, objs_col1, l1)):
                        if not col_objs:
                            continue
                        last_obj = col_objs[-1]
                        obj_end = _object_with_caption_bottom(last_obj, lines, body_size)
                        has_text_below = any(ln.y_base > obj_end + 2.0 for ln in col_lns)
                        blank_gap = barrier_y - obj_end
                        if not has_text_below and blank_gap >= rules.blank_below_obj_tol:
                            c_left = rules.col0_left if col == 0 else rules.col1_left
                            c_right = rules.col0_right if col == 0 else rules.col1_right
                            sev = "fatal" if (col == 0 or blank_gap >= 55.0) else "error"
                            issues.append(
                                Issue(
                                    page=page_num,
                                    category="kolom",
                                    severity=sev,
                                    title=f"Ruang kosong besar di bawah objek (kolom {'kiri' if col == 0 else 'kanan'})",
                                    message=(
                                        f"Tersisa ruang kosong ~{blank_gap:.1f} pt di bawah {last_obj.kind} "
                                        f"tanpa teks pada kolom {'kiri' if col == 0 else 'kanan'}."
                                    ),
                                    suggestion="Isi ruang di bawah gambar/tabel dengan teks atau pindahkan objek ke dasar kolom/halaman lain.",
                                    bbox=(c_left, obj_end, c_right, barrier_y),
                                    column=col,
                                )
                            )

                # Cek kolom kiri menggantung & kolom kanan pendek di segmen bawah pada halaman non-akhir
                if is_bottom_seg and page_num < page_count:
                    target_base = min(ref_bottom_base, eff_bottom - 2.6)
                    closed_by_full_bottom = any(
                        o.is_full_width and o.height >= 28.0 and o.y1 >= eff_bottom - 24.0 for o in objects
                    )
                    if not closed_by_full_bottom:
                        max_obj0 = max((_object_with_caption_bottom(o, lines, body_size) for o in objs_col0), default=0.0)
                        max_obj1 = max((_object_with_caption_bottom(o, lines, body_size) for o in objs_col1), default=0.0)
                        if (l0 or objs_col0) and not col0_closed_by_obj:
                            occ_0 = max(max((ln.y_base for ln in l0), default=0.0), max_obj0)
                            gap_kiri = target_base - occ_0
                            if gap_kiri >= rules.left_hang_tol:
                                sev_kiri = "fatal" if gap_kiri >= 22.0 else "error"
                                issues.append(
                                    Issue(
                                        page=page_num,
                                        category="kolom",
                                        severity=sev_kiri,
                                        title="Kolom kiri menggantung (kurang penuh)",
                                        message=(
                                            f"Kolom kiri berhenti di y={occ_0:.1f} pt, menyisakan ruang kosong "
                                            f"~{gap_kiri:.1f} pt sebelum batas bawah ({target_base:.1f} pt)."
                                        ),
                                        suggestion="Alirkan tambahan teks atau atur penempatan objek agar kolom kiri penuh hingga batas bawah.",
                                        bbox=(rules.col0_left, occ_0, rules.col0_right, eff_bottom),
                                        column=0,
                                    )
                                )
                        if l1 and not col1_closed_by_obj and not objs_col1:
                            max_y1 = max(ln.y_base for ln in l1)
                            ref_kiri = max(max((ln.y_base for ln in l0), default=0.0), max_obj0) if (l0 or objs_col0) else target_base
                            gap_kanan = min(target_base, ref_kiri) - max_y1
                            if gap_kanan >= rules.right_short_tol:
                                issues.append(
                                    Issue(
                                        page=page_num,
                                        category="kolom",
                                        severity="error",
                                        title="Kolom kanan menggantung terlalu pendek",
                                        message=(
                                            f"Kolom kanan berhenti di y={max_y1:.1f} pt, lebih pendek {gap_kanan:.1f} pt "
                                            f"(melebihi toleransi 1 baris / 11 pt)."
                                        ),
                                        suggestion="Tarik teks dari halaman berikutnya atau sejajarkan penutup kolom.",
                                        bbox=(rules.col1_left, max_y1, rules.col1_right, eff_bottom),
                                        column=1,
                                    )
                                )
                        elif not l1 and not objs_col1 and (l0 or objs_col0) and not is_bottom_seg:
                            pass
                        elif not l1 and not objs_col1 and full_objs and eff_bottom - full_objs[-1].y1 >= 44.0 and (l0 or objs_col0):
                            fo_last = full_objs[-1]
                            issues.append(
                                Issue(
                                    page=page_num,
                                    category="kolom",
                                    severity="fatal",
                                    title="Kolom kanan kosong di bawah objek lebar penuh",
                                    message=f"Terdapat teks di kolom kiri bawah objek (y > {fo_last.y1:.1f} pt) namun kolom kanan kosong.",
                                    suggestion="Seimbangkan teks ke kedua kolom atau pindahkan objek ke posisi lain.",
                                    bbox=(rules.col1_left, fo_last.y1, rules.col1_right, eff_bottom),
                                    column=1,
                                )
                            )

            # 5. Cek Grid 11 pt antar baris narasi (tidak memeriksa footer, heading, dan rumus)
            off_grid_count = 0
            eq_obj_ids = {id(ln) for ln in equation_lines_all}
            if rules.check_grid and len(narrative_lines) >= 2:
                col_phases = []
                for col_lns in (page_col0_lines, page_col1_lines):
                    non_hdg = [
                        ln for ln in col_lns
                        if not _is_heading_line(ln, body_size) and id(ln) not in eq_obj_ids
                    ]
                    if non_hdg:
                        col_phases.append(non_hdg[0].y_base)
                phase = col_phases[0] if col_phases else rules.baseline_phase
                off_lines: List[TextLine] = []
                for ln in narrative_lines:
                    if _is_heading_line(ln, body_size) or id(ln) in eq_obj_ids:
                        continue
                    rem = ((ln.y_base - phase) % rules.grid_pt + rules.grid_pt / 2.0) % rules.grid_pt - rules.grid_pt / 2.0
                    if abs(rem) > rules.grid_tol:
                        off_lines.append(ln)
                off_grid_count = len(off_lines)
                if off_lines:
                    first_off = off_lines[0]
                    issues.append(
                        Issue(
                            page=page_num,
                            category="grid",
                            severity="warning",
                            title=f"{off_grid_count} baris tidak sejajar grid {rules.grid_pt:.0f} pt",
                            message=(
                                f"Ditemukan {off_grid_count} baris narasi di halaman {page_num} yang melenceng "
                                f"dari baseline grid {rules.grid_pt:.0f} pt (contoh: \"{first_off.text[:35]}\")."
                            ),
                            suggestion="Aktifkan 'Align to Baseline Grid' (11 pt) pada Paragraph Style di Scribus/PageMaker.",
                            bbox=first_off.bbox,
                            column=first_off.column if first_off.column in (0, 1) else None,
                        )
                    )

            page_metrics.append(
                {
                    "page": page_num,
                    "left_lines": len(page_col0_lines),
                    "right_lines": len(page_col1_lines),
                    "left_bottom": round(max((l.y_base for l in page_col0_lines), default=0.0), 1),
                    "right_bottom": round(max((l.y_base for l in page_col1_lines), default=0.0), 1),
                    "objects": len(objects),
                    "off_grid_lines": off_grid_count,
                }
            )

        # 6. Deteksi 3+ objek berurutan tanpa teks narasi di antaranya
        sig_objs: List[VisualObject] = []
        for pd in per_page_data:
            p_objs = [o for o in pd["objects"] if o.width >= 45.0 and o.height >= 25.0]
            p_objs.sort(key=lambda o: (o.y0, 0 if o.is_full_width else (1 if o.x0 < rules.gutter_center else 2)))
            sig_objs.extend(p_objs)

        def _has_narrative_between(o1: VisualObject, o2: VisualObject) -> bool:
            if o1.page == o2.page:
                n_lns = per_page_data[o1.page - 1]["narrative_lines"]
                if not o1.is_full_width and not o2.is_full_width:
                    c1 = 0 if o1.x0 < rules.gutter_center else 1
                    c2 = 0 if o2.x0 < rules.gutter_center else 1
                    if c1 == c2:
                        return any(ln.column == c1 and o1.y1 - 2.0 < ln.y_base < o2.y0 + 2.0 for ln in n_lns)
                    # Kolom kiri -> kolom kanan: cek teks di bawah o1 (col 0) atau di atas o2 (col 1)
                    return any(
                        (ln.column == 0 and ln.y_base > o1.y1 - 2.0)
                        or (ln.column == 1 and ln.y_base < o2.y0 + 2.0)
                        for ln in n_lns
                    )
                return any(o1.y1 - 2.0 < ln.y_base < o2.y0 + 2.0 for ln in n_lns)
            # Lintas halaman
            if any(ln.y_base > o1.y1 - 2.0 for ln in per_page_data[o1.page - 1]["narrative_lines"]):
                return True
            for mid_p in range(o1.page + 1, o2.page):
                if per_page_data[mid_p - 1]["narrative_lines"]:
                    return True
            if any(ln.y_base < o2.y0 + 2.0 for ln in per_page_data[o2.page - 1]["narrative_lines"]):
                return True
            return False

        run_objs: List[VisualObject] = []
        for obj in sig_objs:
            if not run_objs:
                run_objs = [obj]
            elif not _has_narrative_between(run_objs[-1], obj):
                run_objs.append(obj)
            else:
                if len(run_objs) > rules.max_consecutive_objects:
                    first_o = run_objs[0]
                    same_page = [o for o in run_objs if o.page == first_o.page]
                    bx = (
                        min(o.x0 for o in same_page),
                        min(o.y0 for o in same_page),
                        max(o.x1 for o in same_page),
                        max(o.y1 for o in same_page),
                    )
                    issues.append(
                        Issue(
                            page=first_o.page,
                            category="tata_letak_objek",
                            severity="error",
                            title=f"{len(run_objs)} objek berurutan tanpa teks di antaranya",
                            message=(
                                f"Ditemukan {len(run_objs)} gambar/tabel berturut-turut "
                                f"(mulai Hal. {first_o.page}) tanpa teks narasi pemisah."
                            ),
                            suggestion="Sisipkan minimal satu paragraf teks narasi di antara objek gambar/tabel yang berdekatan.",
                            bbox=bx,
                        )
                    )
                run_objs = [obj]
        if len(run_objs) > rules.max_consecutive_objects:
            first_o = run_objs[0]
            same_page = [o for o in run_objs if o.page == first_o.page]
            bx = (
                min(o.x0 for o in same_page),
                min(o.y0 for o in same_page),
                max(o.x1 for o in same_page),
                max(o.y1 for o in same_page),
            )
            issues.append(
                Issue(
                    page=first_o.page,
                    category="tata_letak_objek",
                    severity="error",
                    title=f"{len(run_objs)} objek berurutan tanpa teks di antaranya",
                    message=(
                        f"Ditemukan {len(run_objs)} gambar/tabel berturut-turut "
                        f"(mulai Hal. {first_o.page}) tanpa teks narasi pemisah."
                    ),
                    suggestion="Sisipkan minimal satu paragraf teks narasi di antara objek gambar/tabel yang berdekatan.",
                    bbox=bx,
                )
            )

        # 6b. Cek Orphan Heading per segmen (dapat melihat heading pembuka di segmen berikutnya)
        for s_idx, seg in enumerate(all_segments):
            col_lns = seg["lines"]
            page_num = seg["page"]
            col = seg["col"]
            objects = per_page_data[page_num - 1]["objects"]
            next_seg_lines = all_segments[s_idx + 1]["lines"] if s_idx + 1 < len(all_segments) else []
            i = 0
            while i < len(col_lns):
                if not _is_heading_line(col_lns[i], body_size):
                    i += 1
                    continue
                j = i
                while j + 1 < len(col_lns) and _is_heading_line(col_lns[j + 1], body_size):
                    if col_lns[j + 1].y_base - col_lns[j].y_base <= body_size * 1.55:
                        j += 1
                    else:
                        break
                hdg_lines = col_lns[i:j + 1]
                hdg_text = " ".join(ln.text for ln in hdg_lines)
                tail_lines = col_lns[j + 1:]
                body_follow = 0
                for tln in tail_lines:
                    if _is_heading_line(tln, body_size):
                        break
                    body_follow += 1

                followed_by_heading = bool(tail_lines and _is_heading_line(tail_lines[0], body_size))
                complete_short_section = (
                    body_follow >= 1
                    and len(tail_lines) > body_follow
                    and _is_heading_line(tail_lines[body_follow], body_size)
                )
                complete_short_cross_col = (
                    body_follow >= 1
                    and len(tail_lines) == body_follow
                    and bool(next_seg_lines)
                    and _is_heading_line(next_seg_lines[0], body_size)
                    and bool(RE_SHORT_SECTION_HEADING.search(hdg_text))
                    and tail_lines[body_follow - 1].text.rstrip().endswith((".", "!", "?", '"', "”", ")", "]"))
                )
                hb = (
                    min(ln.x0 for ln in hdg_lines),
                    min(ln.y0 for ln in hdg_lines),
                    max(ln.x1 for ln in hdg_lines),
                    max(ln.y1 for ln in hdg_lines),
                )
                is_excerpt_above_obj = (
                    body_follow == 0
                    and bool(re.match(r"^Excerpt\s+\d+\b", hdg_text, re.I))
                    and any(o.y0 >= hb[3] - 4.0 and o.y0 <= hb[3] + 85.0 for o in objects)
                )
                if (
                    not followed_by_heading
                    and not is_excerpt_above_obj
                    and not complete_short_section
                    and not complete_short_cross_col
                    and body_follow < rules.min_heading_lines
                ):
                    sev = "fatal" if body_follow == 0 else "error"
                    issues.append(
                        Issue(
                            page=page_num,
                            category="orphan_heading",
                            severity=sev,
                            title=(
                                "Orphan heading sendirian di dasar kolom (0 baris isi)"
                                if body_follow == 0
                                else f"Heading hanya diikuti {body_follow} baris teks (min. {rules.min_heading_lines})"
                            ),
                            message=(
                                f"Subjudul \"{hdg_text[:55]}\" hanya memiliki {body_follow} baris paragraf "
                                f"di bawahnya pada kolom {'kiri' if col == 0 else 'kanan'}."
                            ),
                            suggestion=(
                                "Pindahkan subjudul ini ke puncak kolom berikutnya atau aktifkan "
                                "'Keep with next paragraph' di Scribus/PageMaker."
                            ),
                            bbox=hb,
                            column=col,
                        )
                    )
                i = j + 1

        # 7. Cek Widow & Orphan lintas segmen kolom
        leading = rules.grid_pt
        for s_idx, seg in enumerate(all_segments):
            lns: List[TextLine] = seg["lines"]
            eq_set: set = seg.get("eq_set", set())
            pnum: int = seg["page"]
            col: int = seg["col"]
            prev_seg_lines: List[TextLine] = all_segments[s_idx - 1]["lines"] if s_idx > 0 else []
            prev_eq_set: set = all_segments[s_idx - 1].get("eq_set", set()) if s_idx > 0 else set()
            next_seg_lines: List[TextLine] = all_segments[s_idx + 1]["lines"] if s_idx + 1 < len(all_segments) else []
            next_eq_set: set = all_segments[s_idx + 1].get("eq_set", set()) if s_idx + 1 < len(all_segments) else set()

            # Widow: baris pertama segmen (bukan awal dokumen) adalah 1 baris akhir paragraf sebelumnya
            if s_idx > 0 and lns and 0 not in eq_set and not _is_heading_line(lns[0], body_size):
                first_ln = lns[0]
                prev_last = prev_seg_lines[-1] if prev_seg_lines else None
                prev_last_is_eq = (len(prev_seg_lines) - 1) in prev_eq_set
                prev_ended_para = (
                    prev_last is not None
                    and (prev_last_is_eq or _ends_paragraph(prev_last, None, leading, rules))
                )
                next_in_seg = lns[1] if len(lns) > 1 else None
                starts_fresh = _is_para_start(first_ln, prev_last, leading, rules) if prev_last else False
                col_left = rules.col0_left if col == 0 else rules.col1_left
                indented = (first_ln.x0 - col_left) >= 6.0
                if (
                    not prev_ended_para
                    and not indented
                    and _ends_paragraph(first_ln, next_in_seg, leading, rules)
                    and not starts_fresh
                ):
                    issues.append(
                        Issue(
                            page=pnum,
                            category="widow",
                            severity="error",
                            title="Widow: 1 baris terakhir paragraf di puncak kolom",
                            message=f"Baris \"{first_ln.text[:55]}\" berdiri sendirian di puncak kolom {'kiri' if col == 0 else 'kanan'}.",
                            suggestion="Aktifkan Widow Control (min. 2 baris) atau tarik 1 baris tambahan ke kolom ini.",
                            bbox=first_ln.bbox,
                            column=col,
                        )
                    )

            # Orphan: baris terakhir segmen (bukan akhir dokumen) adalah baris pertama paragraf yang berlanjut
            last_idx = len(lns) - 1
            if s_idx + 1 < len(all_segments) and lns and last_idx not in eq_set and not _is_heading_line(lns[-1], body_size):
                last_ln = lns[-1]
                prev_in_seg = lns[-2] if len(lns) >= 2 else None
                next_first = next_seg_lines[0] if next_seg_lines else None
                next_first_is_eq = 0 in next_eq_set
                col_left = rules.col0_left if col == 0 else rules.col1_left
                col_right = rules.col0_right if col == 0 else rules.col1_right
                started_para = (
                    (last_ln.x0 - col_left) >= 6.0
                    or (prev_in_seg is not None and _is_para_start(last_ln, prev_in_seg, leading, rules))
                )
                ends_sentence = last_ln.text.rstrip().endswith((".", "!", "?", '"', "”", ")"))
                not_short_end = (col_right - last_ln.x1) < (22.0 if ends_sentence else 35.0)
                next_continues = (
                    next_first is not None
                    and not next_first_is_eq
                    and not _is_heading_line(next_first, body_size)
                    and (next_first.x0 - (rules.col0_left if next_first.column == 0 else rules.col1_left)) < 6.0
                )
                if started_para and not_short_end and next_continues:
                    issues.append(
                        Issue(
                            page=pnum,
                            category="orphan",
                            severity="error",
                            title="Orphan: 1 baris awal paragraf sendirian di dasar kolom",
                            message=f"Baris pembuka \"{last_ln.text[:55]}\" terpisah sendirian di dasar kolom {'kiri' if col == 0 else 'kanan'}.",
                            suggestion="Aktifkan Orphan Control (min. 2 baris) atau pindahkan baris pembuka ke kolom berikutnya.",
                            bbox=last_ln.bbox,
                            column=col,
                        )
                    )

        sev_rank = {"fatal": 0, "error": 1, "warning": 2}
        issues.sort(key=lambda i: (i.page, sev_rank.get(i.severity, 9), i.bbox[1] if i.bbox else 0.0))

        summary: Dict[str, int] = {k: 0 for k in CATEGORY_LABELS}
        for iss in issues:
            summary[iss.category] = summary.get(iss.category, 0) + 1

        has_failure = any(iss.severity in ("fatal", "error") for iss in issues)
        return QAReport(
            file_path=abs_path,
            file_name=os.path.basename(abs_path),
            page_count=page_count,
            status="PERLU PERBAIKAN" if has_failure else "LULUS",
            issues=issues,
            page_metrics=page_metrics,
            summary_by_category=summary,
            rules_used=rules.to_dict(),
        )
    finally:
        doc.close()


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    fill: Tuple[int, int, int, int],
    width: int = 1,
    dash: int = 6,
) -> None:
    x0, y0 = p0
    x1, y1 = p1
    total = math.hypot(x1 - x0, y1 - y0)
    if total <= 0:
        return
    dx, dy = (x1 - x0) / total, (y1 - y0) / total
    pos = 0.0
    while pos < total:
        end = min(pos + dash, total)
        draw.line(
            [(x0 + dx * pos, y0 + dy * pos), (x0 + dx * end, y0 + dy * end)],
            fill=fill,
            width=width,
        )
        pos += dash * 2


def render_annotated_page(
    pdf_path: str,
    page_num: int,
    issues: List[Issue],
    rules: Optional[JournalRules] = None,
    dpi: int = 130,
    show_guides: bool = True,
    highlight_issue_idx: Optional[int] = None,
) -> Image.Image:
    """Render satu halaman PDF sebagai gambar RGB dengan garis margin & kotak pelanggaran."""
    if rules is None:
        rules = JournalRules()
    scale = dpi / 72.0
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        base = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
    finally:
        doc.close()

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if show_guides:
        cyan = (0, 150, 199, 175)
        mx0, my0 = rules.margin_left * scale, rules.margin_top * scale
        mx1, my1 = rules.margin_right * scale, rules.content_bottom * scale
        c0r, c1l = rules.col0_right * scale, rules.col1_left * scale
        _draw_dashed_line(draw, (mx0, my0), (mx1, my0), cyan, width=1)
        _draw_dashed_line(draw, (mx0, my1), (mx1, my1), cyan, width=1)
        _draw_dashed_line(draw, (mx0, my0), (mx0, my1), cyan, width=1)
        _draw_dashed_line(draw, (mx1, my0), (mx1, my1), cyan, width=1)
        _draw_dashed_line(draw, (c0r, my0), (c0r, my1), (0, 180, 216, 150), width=1)
        _draw_dashed_line(draw, (c1l, my0), (c1l, my1), (0, 180, 216, 150), width=1)

    font = ImageFont.load_default()
    for idx, iss in enumerate(issues):
        if iss.page != page_num or not iss.bbox:
            continue
        x0, y0, x1, y1 = (v * scale for v in iss.bbox)
        is_selected = highlight_issue_idx == idx
        if is_selected:
            fill_col = (37, 99, 235, 65)
            border_col = (29, 78, 216, 255)
            bw = 3
        elif iss.severity in ("fatal", "error"):
            fill_col = (220, 38, 38, 48)
            border_col = (220, 38, 38, 235)
            bw = 2
        else:
            fill_col = (234, 88, 12, 42)
            border_col = (234, 88, 12, 225)
            bw = 2
        draw.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], fill=fill_col, outline=border_col, width=bw)
        badge_txt = f"#{idx + 1}"
        bx, by = max(2, int(x0) - 2), max(2, int(y0) - 16)
        draw.rectangle([bx, by, bx + 26, by + 14], fill=border_col)
        draw.text((bx + 3, by + 2), badge_txt, fill=(255, 255, 255, 255), font=font)

    combined = Image.alpha_composite(base, overlay).convert("RGB")
    return combined


def export_html_report(report: QAReport, output_html_path: str, max_preview_pages: int = 30) -> str:
    """Ekspor laporan QA visual lengkap dalam satu file HTML mandiri."""
    rules = JournalRules(**{k: v for k, v in report.rules_used.items() if k in JournalRules.__dataclass_fields__})
    pages_with_issues = sorted({iss.page for iss in report.issues})[:max_preview_pages]

    cards_html: List[str] = []
    for k, label in CATEGORY_LABELS.items():
        cnt = report.summary_by_category.get(k, 0)
        cls = "ok" if cnt == 0 else "bad"
        cards_html.append(
            f'<div class="card {cls}"><div class="num">{cnt}</div><div class="lbl">{html.escape(label)}</div></div>'
        )

    rows_html: List[str] = []
    for idx, iss in enumerate(report.issues, 1):
        kol = "Kiri" if iss.column == 0 else ("Kanan" if iss.column == 1 else "Penuh/Halaman")
        rows_html.append(
            f"<tr>"
            f"<td>#{idx}</td>"
            f"<td><span class='badge {html.escape(iss.severity)}'>{html.escape(iss.severity_label)}</span></td>"
            f"<td>Hal. {iss.page} ({kol})</td>"
            f"<td>{html.escape(iss.category_label)}</td>"
            f"<td><strong>{html.escape(iss.title)}</strong><br><small>{html.escape(iss.message)}</small></td>"
            f"<td>{html.escape(iss.suggestion)}</td>"
            f"</tr>"
        )

    previews_html: List[str] = []
    for pnum in pages_with_issues:
        img = render_annotated_page(report.file_path, pnum, report.issues, rules=rules, dpi=115, show_guides=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        p_issues = [i for i in report.issues if i.page == pnum]
        previews_html.append(
            f"<div class='preview-card'>"
            f"<h3>Halaman {pnum} ({len(p_issues)} temuan)</h3>"
            f"<img src='data:image/png;base64,{b64}' alt='Halaman {pnum}'/>"
            f"</div>"
        )

    status_cls = "pass" if report.status == "LULUS" else "fail"
    doc_html = f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<title>Laporan QA Layout - {html.escape(report.file_name)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 24px; background: #f8fafc; color: #0f172a; }}
  .hdr {{ display: flex; justify-content: space-between; align-items: center; background: #fff; padding: 18px 22px; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 18px; }}
  .status {{ font-size: 18px; font-weight: 700; padding: 8px 18px; border-radius: 8px; color: #fff; }}
  .status.pass {{ background: #16a34a; }}
  .status.fail {{ background: #dc2626; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 20px; }}
  .card {{ background: #fff; padding: 12px; border-radius: 8px; border-left: 4px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .card.ok {{ border-left-color: #16a34a; }}
  .card.bad {{ border-left-color: #dc2626; }}
  .card .num {{ font-size: 22px; font-weight: 700; }}
  .card .lbl {{ font-size: 12px; color: #475569; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.06); margin-bottom: 24px; }}
  th, td {{ padding: 10px 12px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: top; font-size: 13px; }}
  th {{ background: #f1f5f9; font-weight: 600; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; color: #fff; }}
  .badge.fatal {{ background: #991b1b; }}
  .badge.error {{ background: #dc2626; }}
  .badge.warning {{ background: #d97706; }}
  .previews {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 18px; }}
  .preview-card {{ background: #fff; padding: 14px; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .preview-card img {{ width: 100%; height: auto; border: 1px solid #cbd5e1; }}
</style>
</head>
<body>
  <div class="hdr">
    <div>
      <h1 style="margin:0 0 4px 0;font-size:20px;">Laporan Pemeriksaan QA Tata Letak Jurnal</h1>
      <div style="color:#475569;font-size:13px;">Berkas: <strong>{html.escape(report.file_name)}</strong> | Total Halaman: <strong>{report.page_count}</strong> | Total Temuan: <strong>{len(report.issues)}</strong></div>
    </div>
    <div class="status {status_cls}">{html.escape(report.status)}</div>
  </div>
  <div class="grid">{"".join(cards_html)}</div>
  <table>
    <thead><tr><th>No</th><th>Tingkat</th><th>Lokasi</th><th>Kategori</th><th>Temuan</th><th>Saran Perbaikan</th></tr></thead>
    <tbody>{"".join(rows_html) if rows_html else '<tr><td colspan="6">Selamat! Seluruh halaman memenuhi aturan tata letak jurnal.</td></tr>'}</tbody>
  </table>
  <h2>Pratinjau Halaman Bermasalah</h2>
  <div class="previews">{"".join(previews_html) if previews_html else "<p>Tidak ada halaman bermasalah.</p>"}</div>
</body>
</html>
"""
    out_dir = os.path.dirname(os.path.abspath(output_html_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(doc_html)
    return os.path.abspath(output_html_path)
