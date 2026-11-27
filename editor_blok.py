#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
editor_blok.py — mesin dokumen blok Layout Editor.

Peran:
  * migrasi body.typ lama -> dokumen.json (setiap blok punya id + style mikro),
  * menulis kembali body.typ dari dokumen.json tanpa merusak baris tata letak lain,
  * kompilasi cepat SVG + layout_map.json + laporan QA,
  * riwayat undo/redo (history/), sisip/hapus blok, dan perbaikan kolom otomatis.

Dipakai oleh api.php:
  python3 editor_blok.py get        kerja/<nama>
  python3 editor_blok.py update     kerja/<nama> --json berkas.json
  python3 editor_blok.py insert     kerja/<nama> --json berkas.json
  python3 editor_blok.py delete     kerja/<nama> --json berkas.json
  python3 editor_blok.py undo       kerja/<nama>
  python3 editor_blok.py redo       kerja/<nama>
  python3 editor_blok.py auto_fix   kerja/<nama> --json berkas.json
  python3 editor_blok.py page       kerja/<nama> --json berkas.json
  python3 editor_blok.py migrate    kerja/<nama>

Semua keluaran JSON di stdout; diagnostik di stderr.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:  # Linux; dipakai untuk menserialisasi pratinjau per artikel.
    import fcntl
except ImportError:  # pragma: no cover - platform non-POSIX
    fcntl = None

BASE = os.path.dirname(os.path.abspath(__file__))
KERJA = os.path.join(BASE, "kerja")
NAMA_DOKUMEN = "dokumen.json"
FOLDER_RIWAYAT = "history"
FOLDER_SVG = "_svg"
FOLDER_QA = "_editor_qa.json"

# Geometri template_jurnal.typ (pt)
LEBAR_HALAMAN = 595.275590551
TINGGI_HALAMAN = 841.88976378
MARGIN_ATAS = 102.4
MARGIN_BAWAH = 79.0
BAWAH_KOLOM = 762.4
LEBAR_TEKS = 442.2
LEBAR_KOLOM = 213.6
JARAK_KOLOM = 15.0
X_KIRI = 76.535
X_KANAN = X_KIRI + LEBAR_KOLOM + JARAK_KOLOM

BATAS_TRACKING = (-0.4, 0.4)
BATAS_LEBAR_GAMBAR = (20.0, 130.0)
BATAS_SPASI = (-44.0, 132.0)
UKURAN_TABEL = (7.0, 14.0)
UKURAN_TABEL_BAWAAN = 10.0
PAD_SEL = (0.0, 12.0)


# ======================================================================
#  Utilitas umum
# ======================================================================
def baca_teks(path, bawaan=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return bawaan


def baca_json(path, bawaan=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return bawaan


def tulis_json(path, obj, rapi=True):
    sementara = path + ".tmp"
    with open(sementara, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2 if rapi else None)
    os.replace(sementara, path)


def hash_teks(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sekarang():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def angka(x, bawaan=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return bawaan


def fmt_pt(x):
    """Tulis panjang Typst tanpa nol berlebih: 0.1 -> 0.1pt."""
    x = round(float(x), 3)
    if abs(x) < 0.0005:
        return "0pt"
    s = ("%.3f" % x).rstrip("0").rstrip(".")
    return s + "pt"


def nama_sah(nama):
    if not re.match(r"^[A-Za-z0-9_\-]{1,120}$", nama or ""):
        raise ValueError("Nama artikel tidak sah")
    return nama


# ======================================================================
#  Pemindai kurung / pemisah argumen Typst
# ======================================================================
def cari_penutup(s, i, buka, tutup):
    """Indeks penutup yang berpasangan dengan pembuka di s[i]; -1 bila tidak ada."""
    if i < 0 or i >= len(s) or s[i] != buka:
        return -1
    kedalaman = 0
    j = i
    dalam_teks = False
    escape = False
    while j < len(s):
        c = s[j]
        if dalam_teks:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                dalam_teks = False
            j += 1
            continue
        if c == '"':
            dalam_teks = True
        elif s.startswith("//", j):
            k = s.find("\n", j)
            j = len(s) if k < 0 else k
            continue
        elif s.startswith("/*", j):
            k = s.find("*/", j + 2)
            j = len(s) if k < 0 else k + 1
            continue
        elif c == buka:
            kedalaman += 1
        elif c == tutup:
            kedalaman -= 1
            if kedalaman == 0:
                return j
        j += 1
    return -1


def potong_atas(s, pemisah=","):
    """Potong s pada pemisah tingkat atas. Hasil: [(awal, akhir), ...]."""
    hasil, awal, j = [], 0, 0
    dalam_teks = False
    escape = False
    tumpuk = []
    pasangan = {")": "(", "]": "[", "}": "{"}
    while j < len(s):
        c = s[j]
        if dalam_teks:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                dalam_teks = False
            j += 1
            continue
        if c == '"':
            dalam_teks = True
        elif c in "([{":
            tumpuk.append(c)
        elif c in ")]}":
            if tumpuk and tumpuk[-1] == pasangan[c]:
                tumpuk.pop()
        elif c == pemisah and not tumpuk:
            hasil.append((awal, j))
            awal = j + 1
        j += 1
    hasil.append((awal, len(s)))
    return hasil


def urai_argumen(s):
    """'path, a: 1, b: [x]' -> [('', 'path'), ('a','1'), ...] (nilai mentah)."""
    hasil = []
    for a, b in potong_atas(s):
        bagian = s[a:b].strip()
        if not bagian:
            continue
        kunci, nilai = "", bagian
        # titik dua tingkat atas pertama
        for k, (c0, c1) in enumerate(potong_atas(bagian, ":")):
            if c1 != len(bagian):
                kunci = bagian[c0:c1].strip()
                nilai = bagian[c1 + 1:].strip()
                break
        hasil.append((kunci, nilai))
    return hasil


def lepas_kurung(nilai, buka="[", tutup="]"):
    nilai = (nilai or "").strip()
    if nilai.startswith(buka) and nilai.endswith(tutup):
        return nilai[1:-1]
    return nilai


# ======================================================================
#  Gaya bawaan
# ======================================================================
def gaya_teks_bawaan():
    return {"tracking": 0.0, "leading": 0.0, "spacing_above": 0.0,
            "spacing_below": 0.0, "break_before": "none", "keep_with_next": False,
            "no_indent": False,
            "frozen": False, "unlocked_in_freeze": False, "freeze_x": 0.0, "freeze_y": 0.0,
            "freeze_w": 0.0, "freeze_slot_h": 0.0}


def gaya_gambar_bawaan():
    return {"width_pct": 100.0, "span": "column", "placement": "auto",
            "margin_top": 0.0, "margin_bottom": 0.0, "spacing_above": 0.0,
            "caption": "", "crop": None,
            "drop_page": 0, "drop_x": None, "drop_y": None,
            "frozen": False, "unlocked_in_freeze": False, "freeze_x": 0.0, "freeze_y": 0.0,
            "freeze_slot_h": 0.0}


def gaya_tabel_bawaan():
    return {"span": "column", "font_size": UKURAN_TABEL_BAWAAN, "breakable": None,
            "cell_pad": 4.0, "spacing_above": 0.0, "caption": "",
            "crop": None,
            "drop_page": 0, "drop_x": None, "drop_y": None,
            "frozen": False, "unlocked_in_freeze": False, "freeze_x": 0.0, "freeze_y": 0.0,
            "freeze_slot_h": 0.0}


GAYA_BAWAAN = {
    "paragraph": gaya_teks_bawaan, "heading": gaya_teks_bawaan,
    "reference": gaya_teks_bawaan, "list_item": gaya_teks_bawaan,
    "labeled_paragraph": gaya_teks_bawaan, "quote": gaya_teks_bawaan,
    "figure": gaya_gambar_bawaan, "table": gaya_tabel_bawaan,
}


def _normal_drop(gaya):
    """Koordinat drop bebas (pt) hanya untuk anotasi visual editor."""
    gaya["drop_page"] = int(clamp(angka(gaya.get("drop_page"), 0), 0, 9999))
    for kunci, batas in (("drop_x", LEBAR_HALAMAN), ("drop_y", TINGGI_HALAMAN)):
        if gaya.get(kunci) is None:
            continue
        try:
            gaya[kunci] = clamp(float(gaya[kunci]), 0.0, batas)
        except (TypeError, ValueError):
            gaya[kunci] = None


def _normal_crop(raw_crop):
    if isinstance(raw_crop, dict) and not raw_crop.get("reset"):
        cl = clamp(angka(raw_crop.get("left"), 0.0), 0.0, 0.85)
        ct = clamp(angka(raw_crop.get("top"), 0.0), 0.0, 0.85)
        cr = clamp(angka(raw_crop.get("right"), 0.0), 0.0, 0.85 - cl)
        cb = clamp(angka(raw_crop.get("bottom"), 0.0), 0.0, 0.85 - ct)
        if cl >= 0.005 or ct >= 0.005 or cr >= 0.005 or cb >= 0.005:
            return {"left": round(cl, 4), "top": round(ct, 4),
                    "right": round(cr, 4), "bottom": round(cb, 4)}
    return None


def normal_gaya(jenis, gaya):
    dasar = GAYA_BAWAAN.get(jenis, gaya_teks_bawaan)()
    hasil = dict(dasar)
    if isinstance(gaya, dict):
        for k, v in gaya.items():
            if k in hasil or k in ("caption",):
                hasil[k] = v
        if "spacing_above" not in gaya and "vertical_offset" in gaya and "spacing_above" in hasil:
            hasil["spacing_above"] = gaya["vertical_offset"]
    # validasi
    if jenis in ("paragraph", "heading", "reference", "list_item", "labeled_paragraph", "quote"):
        hasil["tracking"] = clamp(angka(hasil.get("tracking"), 0.0), *BATAS_TRACKING)
        hasil["leading"] = clamp(angka(hasil.get("leading"), 0.0), -6.0, 12.0)
        hasil["spacing_above"] = clamp(angka(hasil.get("spacing_above"), 0.0), *BATAS_SPASI)
        hasil["spacing_below"] = clamp(angka(hasil.get("spacing_below"), 0.0), *BATAS_SPASI)
        if hasil.get("break_before") not in ("none", "column", "page"):
            hasil["break_before"] = "none"
        hasil["keep_with_next"] = bool(hasil.get("keep_with_next"))
        hasil["no_indent"] = bool(hasil.get("no_indent", False))
        fw = angka(hasil.get("freeze_w"), 0.0)
        hasil["freeze_w"] = round(clamp(fw, 80.0, LEBAR_TEKS), 3) if fw > 1.0 else 0.0
    elif jenis == "figure":
        style = gaya
        hasil["width_pct"] = clamp(angka(hasil.get("width_pct"), 100.0), *BATAS_LEBAR_GAMBAR)
        if hasil.get("span") not in ("column", "page"):
            hasil["span"] = "column"
        if hasil.get("placement") not in ("auto", "top", "bottom", "none", "float"):
            hasil["placement"] = "auto"
        hasil["margin_top"] = clamp(angka(hasil.get("margin_top"), 0.0), -44.0, 132.0)
        hasil["margin_bottom"] = clamp(angka(hasil.get("margin_bottom"), 0.0), -44.0, 132.0)
        hasil["spacing_above"] = clamp(angka(hasil.get("spacing_above"), 0.0), *BATAS_SPASI)
        hasil["caption"] = str(hasil.get("caption") or "")
        _normal_drop(hasil)
        raw_crop = gaya.get("crop") if isinstance(gaya, dict) else hasil.get("crop")
        hasil["crop"] = _normal_crop(raw_crop)
    elif jenis == "table":
        if hasil.get("span") not in ("column", "page"):
            hasil["span"] = "column"
        hasil["font_size"] = clamp(angka(hasil.get("font_size"), UKURAN_TABEL_BAWAAN), *UKURAN_TABEL)
        hasil["cell_pad"] = clamp(angka(hasil.get("cell_pad"), 4.0), *PAD_SEL)
        hasil["spacing_above"] = clamp(angka(hasil.get("spacing_above"), 0.0), *BATAS_SPASI)
        if hasil.get("breakable") not in (True, False, None):
            hasil["breakable"] = None
        hasil["caption"] = str(hasil.get("caption") or "")
        _normal_drop(hasil)
        raw_crop = gaya.get("crop") if isinstance(gaya, dict) else hasil.get("crop")
        hasil["crop"] = _normal_crop(raw_crop)
    hasil["freeze_x"] = round(clamp(angka(hasil.get("freeze_x"), 0.0), -500.0, 500.0), 3)
    hasil["freeze_y"] = round(clamp(angka(hasil.get("freeze_y"), 0.0), -800.0, 800.0), 3)
    sh = angka(hasil.get("freeze_slot_h"), 0.0)
    hasil["freeze_slot_h"] = round(clamp(sh, 11.0, 760.0), 3) if sh > 1.0 else 0.0
    hasil["unlocked_in_freeze"] = bool(hasil.get("unlocked_in_freeze"))
    if isinstance(gaya, dict) and "frozen" in gaya and gaya["frozen"] is not None:
        hasil["frozen"] = bool(gaya["frozen"])
    else:
        hasil["frozen"] = (bool(hasil.get("frozen")) or abs(hasil["freeze_x"]) > 0.05
                           or abs(hasil["freeze_y"]) > 0.05
                           or hasil.get("freeze_w", 0.0) > 1.0
                           or hasil.get("freeze_slot_h", 0.0) > 1.0)
    return hasil


# ======================================================================
#  Konversi teks Typst <-> teks UI (Markdown ringan)
# ======================================================================
_RE_META = re.compile(r'#metadata\([^)]*\)#label\("[^"]*"\)')
_RE_LABEL = re.compile(r'#label\("[^"]*"\)')
_RE_PE_PF = re.compile(r"#pe\(\d+(?:,\s*bagian:\s*\d+)?\)|#pf\(\d+,\s*\d+\);?")


def _lepas_injeksi_inline(s):
    s = _RE_META.sub("", s)
    s = _RE_PE_PF.sub("", s)
    # buang pembungkus #gaya-teks(...)[ ... ] bila ada
    for _ in range(8):
        m = re.search(r"#gaya-teks\([^)]*\)\[", s)
        if not m:
            break
        b = m.end() - 1
        e = cari_penutup(s, b, "[", "]")
        if e < 0:
            break
        s = s[:m.start()] + s[b + 1:e] + s[e + 1:]
    s = _RE_LABEL.sub("", s)
    return s


def _urai_perintah_typst(s):
    """Ubah markup inline Typst hasil pandoc menjadi markup ringan untuk UI."""
    # perintah berisi satu isi bracket: #emph[...] #strong[...] #super[...] #sub[...] dst.
    peta = {"emph": "_", "strong": "*", "super": "^", "sub": "~", "smallcaps": ""}
    keluar = []
    i = 0
    while i < len(s):
        # escape pasangan (mis. \' atau \$) dan rumus $...$ disalin apa adanya
        if s[i] == "\\" and i + 1 < len(s):
            keluar.append(s[i:i + 2])
            i += 2
            continue
        if s[i] == "$":
            j = s.find("$", i + 1)
            if j > 0:
                keluar.append(s[i:j + 1])
                i = j + 1
                continue
        m = re.match(r"#(emph|strong|super|sub|smallcaps)\[", s[i:])
        if m:
            awal = i + m.end() - 1
            akhir = cari_penutup(s, awal, "[", "]")
            if akhir > 0:
                isi = _urai_perintah_typst(s[awal + 1:akhir])
                perintah = m.group(1)
                if perintah in ("super", "sub"):
                    keluar.append(("^{" if perintah == "super" else "~{") + isi + "}")
                else:
                    tanda = peta[perintah]
                    keluar.append(tanda + isi + (tanda if tanda else ""))
                i = akhir + 1
                continue
        m = re.match(r'#link\("([^"]*)"\)\[', s[i:])
        if m:
            awal = i + m.end() - 1
            akhir = cari_penutup(s, awal, "[", "]")
            if akhir > 0:
                isi = _urai_perintah_typst(s[awal + 1:akhir])
                keluar.append("[%s](%s)" % (isi, m.group(1)))
                i = akhir + 1
                continue
        m = re.match(r"#(?:linebreak\([^)]*\)|parbreak\(\))", s[i:])
        if m:
            keluar.append(" ")
            i += m.end()
            continue
        m = re.match(r"#(?:h|v)\([^)]*\)", s[i:])
        if m:
            i += m.end()
            continue
        m = re.match(r"#raw\([^)]*\)\[", s[i:])
        if m:
            awal = i + m.end() - 1
            akhir = cari_penutup(s, awal, "[", "]")
            if akhir > 0:
                keluar.append(s[awal + 1:akhir])
                i = akhir + 1
                continue
        keluar.append(s[i])
        i += 1
    hasil = "".join(keluar)
    # escape Typst -> teks biasa
    hasil = re.sub(r"\\([\\#*_\[\]{}()\$<>@'\"~^/])", r"\1", hasil)
    hasil = hasil.replace("\\-", "-").replace("\\,", ",")
    hasil = re.sub(r"[ \t]+", " ", hasil)
    return hasil.strip()


def teks_ui_dari_typst(typst):
    return _urai_perintah_typst(_lepas_injeksi_inline(typst or ""))


_RE_UI = re.compile(
    r"\*\*(?P<bold>.+?)\*\*|(?<!\*)\*(?P<bold2>[^*\n]+?)\*(?!\*)|"
    r"(?<!_)_(?P<ital>[^_\n]+?)_(?!_)|"
    r"\^\{(?P<sup>[^}\n]*)\}|~\{(?P<sub>[^}\n]*)\}|"
    r"\[(?P<teks_tautan>[^\]\n]*)\]\((?P<url>[^)\s]+)\)|"
    r"\$(?P<math>[^$\n]+)\$", re.S)


def _escape_typst_teks(s):
    keluar = []
    for c in s:
        # Kurung ikut di-escape: teks UI seperti "_22_(2)" menghasilkan
        # "#emph[22](2)" yang dibaca Typst sebagai pemanggilan fungsi.
        if c in "\\#*_[]$<>@'\"~^`()":
            keluar.append("\\" + c)
        else:
            keluar.append(c)
    return "".join(keluar)


def typst_dari_teks_ui(teks):
    """Markup ringan UI -> markup inline Typst yang aman."""
    teks = (teks or "").replace("\r\n", "\n").replace("\r", "\n")
    teks = teks.replace("\n", " ")
    hasil = []
    pos = 0
    for m in _RE_UI.finditer(teks):
        hasil.append(_escape_typst_teks(teks[pos:m.start()]))
        if m.group("bold") is not None:
            hasil.append("#strong[%s]" % typst_dari_teks_ui(m.group("bold")))
        elif m.group("bold2") is not None:
            hasil.append("#strong[%s]" % typst_dari_teks_ui(m.group("bold2")))
        elif m.group("ital") is not None:
            hasil.append("#emph[%s]" % typst_dari_teks_ui(m.group("ital")))
        elif m.group("sup") is not None:
            hasil.append("#super[%s]" % typst_dari_teks_ui(m.group("sup")))
        elif m.group("sub") is not None:
            hasil.append("#sub[%s]" % typst_dari_teks_ui(m.group("sub")))
        elif m.group("teks_tautan") is not None:
            isi = _escape_typst_teks(m.group("teks_tautan"))
            url = m.group("url").replace('"', '\\"')
            hasil.append('#link("%s")[%s]' % (url, isi))
        elif m.group("math") is not None:
            # rumus inline dipertahankan apa adanya (jangan di-escape)
            hasil.append("$" + m.group("math") + "$")
        pos = m.end()
    hasil.append(_escape_typst_teks(teks[pos:]))
    return "".join(hasil)


def label_dari_caption(caption, cadangan=""):
    m = re.match(r"^\s*((?:Figure|Gambar|Table|Tabel|Fig\.?)\s+\d+[A-Za-z]?)\s*[.:]?\s*(.*)$",
                 caption or "", re.I)
    if m:
        label = m.group(1)
        label = re.sub(r"^Fig\.?\s+", "Figure ", label, flags=re.I)
        return label + ".", m.group(2).strip()
    return cadangan, (caption or "").strip()


def angka_objek_dari_label(label):
    m = re.search(r"(\d+)", label or "")
    return m.group(1) if m else ""


# ======================================================================
#  Format baris body.typ
# ======================================================================
_RE_HEADING = re.compile(r"^(=+)[ \t]+#pm\((\d+)\);(.*)$")
_RE_PARA = re.compile(r"^#pm\((\d+)\);(.*)$")
_RE_REF = re.compile(r"^(#referensi(?:\([^)]*\))?)\[#pm\((\d+)\);(.*)\]\s*$")
_RE_BUTIR = re.compile(r"^(#butir\((?:[^()\n]|\([^)\n]*\))*\))\[#pm\((\d+)\);(.*)\]\s*$")
_RE_LABEL_GANTUNG = re.compile(r"^(#label-gantung\((?:[^()\n]|\([^)\n]*\))*\))\[#pm\((\d+)\);(.*)\]\s*$")
_RE_WAWANCARA = re.compile(r"^(#wawancara)\[#pm\((\d+)\);(.*)\]\s*$")
_RE_LANJUTAN = re.compile(r"^#(lanjutan(?:-ref)?)\[(.*)\]\s*$")
_RE_GAMBAR = re.compile(r"^#gambar\((.*)\)\s*$")
_RE_TABEL = re.compile(r"^#(tabel|tabel-panjang)\(")
_RE_MULAI_TABEL = re.compile(r"^(#(?:tabel|tabel-panjang)\()")

_RE_HAPUS_DIRECTIVE = re.compile(r"//\s*ed:[a-z0-9_-]+")
_RE_BLOK_STICKY = re.compile(r"^#block\(sticky: true\)\[$")


def _buang_penanda_akhir(s):
    s = re.sub(r'#metadata\("akhir"\)#label\("blk-akhir-[^"]*"\)', "", s)
    return _RE_PE_PF.sub("", s).rstrip()


def _bersih_isi_potong_kol(isi_bag, pm_num=None):
    """Lepas penanda fragmen #pf/#pe/#linebreak(justify: true) internal pada segmen #beku-kol."""
    s = isi_bag.strip()
    s = re.sub(r"^#pf\(\d+,\s*\d+\);?", "", s).strip()
    s = re.sub(r"\s*#linebreak\(justify:\s*true\)\s*$", "", s).strip()
    s = re.sub(r"\s*#pe\(\d+(?:,\s*bagian:\s*\d+)?\)\s*(?:#linebreak\(justify:\s*true\)\s*)?$", "", s).strip()
    return s


def _ekstrak_beku_detail(s):
    """Bila s dibungkus #beku(...) atau #beku-kol(...)[...], kembalikan (s_isi, dx, dy, True, tanpa_inden)."""
    m_kol = re.search(r"#beku-kol\(", s)
    m = m_kol or re.search(r"#beku\(", s)
    if not m:
        return s, 0.0, 0.0, False, False
    kp = m.end() - 1
    kt = cari_penutup(s, kp, "(", ")")
    if kt < 0:
        return s, 0.0, 0.0, False, False

    args_str = s[kp + 1:kt]
    parts = [p for p in args_str.split(",") if ":" not in p]
    fx, fy = 0.0, 0.0
    if len(parts) >= 1:
        fx = angka(parts[0].replace("pt", "").strip(), 0.0)
    if len(parts) >= 2:
        fy = angka(parts[1].replace("pt", "").strip(), 0.0)
    tanpa_inden = bool(re.search(r"tanpa-inden\s*:\s*true\b", args_str))

    fx = round(clamp(fx, -500.0, 500.0), 3)
    fy = round(clamp(fy, -800.0, 800.0), 3)

    if m_kol:
        potongan = []
        pos = kt + 1
        while pos < len(s):
            while pos < len(s) and s[pos].isspace():
                pos += 1
            if pos >= len(s) or s[pos] != "[":
                break
            k_end = cari_penutup(s, pos, "[", "]")
            if k_end < 0:
                return s, 0.0, 0.0, False, False
            potongan.append(s[pos + 1:k_end])
            pos = k_end + 1
        if not potongan:
            return s, 0.0, 0.0, False, False
        gaya_pre = ""
        cleaned = []
        for idx, pot in enumerate(potongan):
            mg = re.match(r"^\s*(#gaya-teks\([^)]*\)\[)", pot)
            pot_work = pot
            if mg and pot.rstrip().endswith("]"):
                if idx == 0:
                    gaya_pre = mg.group(1)
                pot_work = pot[mg.end():pot.rfind("]")]
            pot_work = _buang_penanda_akhir(pot_work)
            cleaned.append(_bersih_isi_potong_kol(pot_work))
        isi_gabung = " ".join(c for c in cleaned if c)
        # Pindahkan #pm(...) yang sudah ada di cleaned[0] dan tutup dengan #pe(pm) bila ada
        m_pm = re.match(r"^#pm\((\d+)\);", isi_gabung)
        if m_pm:
            isi_gabung = "%s #pe(%s)" % (isi_gabung, m_pm.group(1))
        if gaya_pre:
            isi_gabung = gaya_pre + isi_gabung + "]"
        isi = s[:m.start()] + isi_gabung + s[pos:]
        return isi, fx, fy, True, tanpa_inden

    kb = s.find("[", kt)
    if kb < 0:
        return s, 0.0, 0.0, False, False
    ktutup = cari_penutup(s, kb, "[", "]")
    if ktutup < 0:
        return s, 0.0, 0.0, False, False

    isi = s[:m.start()] + s[kb + 1:ktutup] + s[ktutup + 1:]
    return isi, fx, fy, True, tanpa_inden


def _ekstrak_beku_extra(s):
    """Ambil parameter freeze-w, slot-h, freeze-leading, freeze-tracking dari #beku(...)."""
    extra = {}
    m = re.search(r"#beku\(", s)
    if not m:
        return extra
    kp = m.end() - 1
    kt = cari_penutup(s, kp, "(", ")")
    if kt < 0:
        return extra
    args_str = s[kp + 1:kt]
    for kunci, nama, lo, hi in (
        ("freeze-w", "freeze_w", 80.0, LEBAR_TEKS),
        ("slot-h", "freeze_slot_h", 11.0, 760.0),
        ("freeze-leading", "leading", -6.0, 12.0),
        ("freeze-tracking", "tracking", -2.0, 5.0),
    ):
        mm = re.search(r"\b%s\s*:\s*(-?[\d.]+)\s*pt\b" % re.escape(kunci), args_str)
        if mm:
            val = angka(mm.group(1), 0.0)
            if nama in ("freeze_w", "freeze_slot_h"):
                if val > 1.0:
                    extra[nama] = round(clamp(val, lo, hi), 3)
            else:
                extra[nama] = round(clamp(val, lo, hi), 3)
    return extra


def _ekstrak_beku(s):
    """Bila s dibungkus #beku(dx, dy, ...)[...], kembalikan (s_isi, dx, dy, True). Bila tidak, (s, 0.0, 0.0, False)."""
    isi, fx, fy, ada, _ = _ekstrak_beku_detail(s)
    return isi, fx, fy, ada


def _buang_gaya_wrapper(s):
    m = re.search(r"#gaya-teks\([^)]*\)\[", s)
    if m:
        b = m.end() - 1
        e = cari_penutup(s, b, "[", "]")
        if e >= 0:
            s = s[:m.start()] + s[b + 1:e] + s[e + 1:]
    s, _, _, _ = _ekstrak_beku(s)
    return s


def _urai_baris_teks(baris):
    """Kembalikan dict blok dari satu baris ber-#pm, atau None."""
    baris = _buang_penanda_akhir(baris)
    beku_extra = _ekstrak_beku_extra(baris)
    baris, fx, fy, ada_beku, tanpa_inden = _ekstrak_beku_detail(baris)
    baris = _buang_gaya_wrapper(baris)
    res = None
    m = _RE_HEADING.match(baris)
    if m:
        res = {"jenis": "heading", "level": len(m.group(1)), "pm": int(m.group(2)),
               "content": m.group(3), "prefix": m.group(1) + " "}
    else:
        m = _RE_PARA.match(baris)
        if m:
            res = {"jenis": "paragraph", "pm": int(m.group(1)), "content": m.group(2), "prefix": ""}
        else:
            m = _RE_REF.match(baris)
            if m:
                res = {"jenis": "reference", "pm": int(m.group(2)), "content": m.group(3),
                       "prefix": m.group(1) + "["}
            else:
                m = _RE_BUTIR.match(baris)
                if m:
                    res = {"jenis": "list_item", "pm": int(m.group(2)), "content": m.group(3),
                           "prefix": m.group(1) + "["}
                else:
                    m = _RE_LABEL_GANTUNG.match(baris)
                    if m:
                        res = {"jenis": "labeled_paragraph", "pm": int(m.group(2)), "content": m.group(3),
                               "prefix": m.group(1) + "["}
                    else:
                        m = _RE_WAWANCARA.match(baris)
                        if m:
                            res = {"jenis": "quote", "pm": int(m.group(2)), "content": m.group(3),
                                   "prefix": m.group(1) + "["}
    if res:
        if tanpa_inden:
            res["no_indent"] = True
        if ada_beku or abs(fx) > 0.0005 or abs(fy) > 0.0005 or beku_extra:
            res["frozen"] = True
            res["freeze_x"] = fx
            res["freeze_y"] = fy
            res.update(beku_extra)
        return res
    return None


# ======================================================================
#  Parsing body.typ -> blok
# ======================================================================
def tambah_penanda_body(body):
    """Migrasi artikel lama tanpa #pm: sisipkan penanda nol-lebar pada paragraf/judul/
    referensi/butir. Tidak mengubah tampilan; hanya agar blok dapat dipetakan editor."""
    if "#pm(" in body:
        return body
    baris = body.split("\n")
    keluar, n, i = [], 0, 0
    while i < len(baris):
        b = baris[i]
        # lewati seluruh blok tabel (multi-baris) apa adanya
        if re.match(r"^#(tabel|tabel-panjang)\(", b):
            sisa = "\n".join(baris[i:])
            kp = sisa.find("(")
            kt = cari_penutup(sisa, kp, "(", ")") if kp >= 0 else -1
            kb = sisa.find("[", kt) if kt >= 0 else -1
            ktutup = cari_penutup(sisa, kb, "[", "]") if kb >= 0 else -1
            if ktutup >= 0:
                jumlah = sisa[:ktutup].count("\n") + 1
                keluar.extend(baris[i:i + jumlah])
                i += jumlah
                continue
        m = re.match(r"^(#referensi(?:\([^)]*\))?\[|#butir\([^\n]*\)\[|"
                     r"#label-gantung\([^\n]*\)\[|#wawancara\[)(.*)$", b)
        if m:
            n += 1
            keluar.append(m.group(1) + "#pm(%d);" % n + m.group(2))
            i += 1
            continue
        m = re.match(r"^(=+)\s+(.*)$", b)
        if m:
            n += 1
            keluar.append(m.group(1) + " #pm(%d);" % n + m.group(2))
            i += 1
            continue
        if (b.strip() and not b.lstrip().startswith(("#", "=", "/", "[", "]", "|", "$", "`", "- ", "+ ", "* "))
                and not b.lstrip().startswith("//")):
            n += 1
            keluar.append("#pm(%d);" % n + b)
            i += 1
            continue
        keluar.append(b)
        i += 1
    return "\n".join(keluar)


def tambah_nomor_objek_body(body):
    """Artikel lama (mode mengambang) belum menulis nomor objek. Beri nomor unik
    agar gambar/tabel dapat dipetakan editor tanpa mengubah tata letak."""
    if not re.search(r"^#(?:gambar|tabel|tabel-panjang)\(", body, flags=re.M):
        return body
    pola = re.compile(r"^#(?:gambar|tabel|tabel-panjang)\(")
    if not any(pola.match(b) and not re.search(r"\bnomor:\s*\d+", b) for b in body.split("\n")):
        return body
    maks = max([int(x) for x in re.findall(r"\bnomor:\s*(\d+)", body)] or [0])
    n = maks
    keluar = []
    for b in body.split("\n"):
        if pola.match(b) and not re.search(r"\bnomor:\s*\d+", b):
            n += 1
            if "label:" in b:
                b = b.replace("label:", "nomor: %d, label:" % n, 1)
            elif b.startswith("#gambar(") and b.rstrip().endswith(")"):
                b = b.rstrip()[:-1] + ", nomor: %d)" % n
            elif ")[" in b:
                b = b.replace(")[", ", nomor: %d)[" % n, 1)
        keluar.append(b)
    return "\n".join(keluar)


def _pecah_baris(body):
    return body.split("\n")


def _baris_ke_indeks(baris):
    return len("\n".join(baris))


def parse_body(body):
    """Urai body.typ menjadi blok logis (tanpa id/style)."""
    baris = _pecah_baris(body)
    blok = []
    i = 0
    n_baris = len(baris)
    pending_break = None
    while i < n_baris:
        b = baris[i]
        b_clean = re.sub(r"//.*$", "", b).strip()
        if b_clean in ("#colbreak()", "#colbreak(weak: true)"):
            pending_break = "column"
            i += 1
            continue
        if b_clean in ("#pagebreak()", "#pagebreak(weak: true)"):
            pending_break = "page"
            i += 1
            continue
        if not b.strip() or b.lstrip().startswith("//"):
            i += 1
            continue
        if b.startswith("#import ") or b.startswith("#kaki-naik(") or b.startswith("#kepala-turun("):
            pending_break = None
            i += 1
            continue
        # tabel multi-baris
        mt = _RE_MULAI_TABEL.match(b)
        if mt:
            pending_break = None
            sisa = "\n".join(baris[i:])
            kp = sisa.find("(")
            kt = cari_penutup(sisa, kp, "(", ")") if kp >= 0 else -1
            if kt < 0:
                i += 1
                continue
            kb = sisa.find("[", kt)
            if kb < 0:
                i += 1
                continue
            ktutup = cari_penutup(sisa, kb, "[", "]")
            if ktutup < 0:
                i += 1
                continue
            args = urai_argumen(sisa[kp + 1:kt])
            panggil = {"fungsi": mt.group(1).lstrip("#").rstrip("("), "args": {}, "urutan": []}
            for k, v in args:
                if k:
                    panggil["args"][k] = v
                    panggil["urutan"].append(k)
                else:
                    panggil["path"] = v
            isi_tabel = sisa[kb + 1:ktutup]
            try:
                jml_kol = int(panggil["args"].get("jumlah-kolom", "0") or 0)
            except ValueError:
                jml_kol = 0
            penuh_mentah = panggil["args"].get("penuh")
            penuh_tb = (penuh_mentah.strip() == "true") if penuh_mentah is not None \
                else (jml_kol > 3 or panggil["fungsi"] == "tabel-panjang")
            blok.append({"jenis": "table", "panggil": panggil, "table_body": isi_tabel,
                         "nomor": int(panggil["args"].get("nomor", "0") or 0),
                         "caption_raw": lepas_kurung(panggil["args"].get("isi", "[]")),
                         "label_raw": lepas_kurung(panggil["args"].get("label", "[]")),
                         "penuh": penuh_tb})
            i += sisa[:ktutup].count("\n") + 1
            continue
        # gambar satu baris
        mg = _RE_GAMBAR.match(b)
        if mg:
            pending_break = None
            args = urai_argumen(mg.group(1))
            panggil = {"fungsi": "gambar", "args": {}, "urutan": []}
            for k, v in args:
                if k:
                    panggil["args"][k] = v
                    panggil["urutan"].append(k)
                else:
                    panggil["path"] = v
            blok.append({"jenis": "figure", "panggil": panggil,
                         "nomor": int(panggil["args"].get("nomor", "0") or 0),
                         "caption_raw": lepas_kurung(panggil["args"].get("isi", "[]")),
                         "label_raw": lepas_kurung(panggil["args"].get("label", "[]")),
                         "penuh": panggil["args"].get("penuh", "false").strip() == "true"
                         or angka(panggil["args"].get("lebar-word", "0pt").replace("pt", "")) > LEBAR_KOLOM * 1.25})
            i += 1
            continue
        # lanjutan hasil ratakan_kolom: gabung ke blok teks sebelumnya
        ml = _RE_LANJUTAN.match(b)
        if ml and blok:
            pending_break = None
            sebelumnya = blok[-1]
            if sebelumnya.get("jenis") in ("paragraph", "reference", "list_item",
                                          "labeled_paragraph", "heading", "quote"):
                tambahan = _buang_penanda_akhir(ml.group(2)).lstrip()
                if sebelumnya.get("content"):
                    sebelumnya["content"] = sebelumnya["content"].rstrip() + " " + tambahan
                else:
                    sebelumnya["content"] = tambahan
                sebelumnya.setdefault("hapus_lanjutan", []).append(i)
                i += 1
                continue
        tb = _urai_baris_teks(b)
        if tb:
            if pending_break:
                tb["break_before"] = pending_break
                pending_break = None
            blok.append(tb)
            i += 1
            continue
        i += 1
    return blok


# ======================================================================
#  Table cells
# ======================================================================
def _kolom_tabel(table_body):
    m = re.search(r"columns:\s*\(([^)]*)\)", table_body)
    if m:
        return max(1, len([x for x in potong_atas(m.group(1)) if m.group(1)[x[0]:x[1]].strip()]))
    m = re.search(r"columns:\s*(\d+)", table_body)
    return max(1, int(m.group(1))) if m else 1


def _parse_sel(s, a, b):
    """Isi satu argumen sel -> dict sel, atau None."""
    p = s[a:b].strip()
    if not p:
        return None
    awal = a + (len(s[a:b]) - len(s[a:b].lstrip()))
    if p.startswith("table.hline") or p.startswith("table.vline") or p.startswith("table.break"):
        return None
    span_r = span_c = 1
    header = False
    if p.startswith("table.header"):
        header = True
        hp = p.find("(")
        hc = cari_penutup(p, hp, "(", ")") if hp >= 0 else -1
        if hc < 0:
            return None
        # sel-sel di dalam header; offset dikembalikan ke koordinat `s`
        dalam = p[hp + 1:hc]
        basis = awal + hp + 1
        hasil = []
        for x0, x1 in potong_atas(dalam):
            sel = _parse_sel(dalam, x0, x1)
            if sel:
                sel["header"] = True
                sel["start"] += basis
                sel["end"] += basis
                hasil.append(sel)
        return hasil
    if p.startswith("table.cell"):
        cp = p.find("(")
        cc = cari_penutup(p, cp, "(", ")") if cp >= 0 else -1
        if cc < 0:
            return None
        for k, v in urai_argumen(p[cp + 1:cc]):
            if k == "rowspan":
                span_r = max(1, int(angka(v, 1)))
            elif k == "colspan":
                span_c = max(1, int(angka(v, 1)))
        kb = p.find("[", cc)
        if kb < 0:
            return None
        kt = cari_penutup(p, kb, "[", "]")
        if kt < 0:
            return None
        return {"start": awal + kb + 1, "end": awal + kt, "content": p[kb + 1:kt],
                "span_r": span_r, "span_c": span_c, "header": header}
    if p.startswith("["):
        kt = cari_penutup(p, 0, "[", "]")
        if kt < 0:
            return None
        return {"start": awal + 1, "end": awal + kt, "content": p[1:kt],
                "span_r": 1, "span_c": 1, "header": header}
    return None


def sel_table(table_body):
    """Daftar sel tabel berurutan dengan posisi absolut di table_body."""
    i = table_body.find("#table(")
    if i < 0:
        return []
    kp = i + len("#table(") - 1
    kt = cari_penutup(table_body, kp, "(", ")")
    if kt < 0:
        return []
    args = table_body[kp + 1:kt]
    basis = kp + 1
    hasil = []
    for a, b in potong_atas(args):
        sel = _parse_sel(args, a, b)
        if not sel:
            continue
        if isinstance(sel, list):
            for s in sel:
                s["start"] += basis
                s["end"] += basis
                hasil.append(s)
        else:
            sel["start"] += basis
            sel["end"] += basis
            hasil.append(sel)
    kolom = _kolom_tabel(table_body)
    r = c = 0
    for s in hasil:
        s["r"], s["c"] = r, c
        c += s.get("span_c", 1)
        if c >= kolom:
            r += 1
            c = 0
    return hasil


def ganti_sel_table(table_body, edits):
    """edits: {index: teks_ui_baru} -> table_body baru."""
    sel = sel_table(table_body)
    urut = []
    for idx, teks in edits.items():
        if 0 <= idx < len(sel):
            urut.append((sel[idx]["start"], sel[idx]["end"], typst_dari_teks_ui(teks)))
    for a, b, baru in sorted(urut, reverse=True):
        table_body = table_body[:a] + baru + table_body[b:]
    return table_body


# ======================================================================
#  Dokumen: migrasi, merge style, metadata
# ======================================================================
LABEL_META = {
    "judul": "Judul Artikel", "penulis": "Penulis", "afiliasi": "Afiliasi",
    "abstrak": "Abstrak", "kata_kunci": "Kata Kunci",
    # Metadata halaman 1 yang sebelumnya hanya ada di meta.json (tidak bisa
    # disentuh dari kanvas). Nilainya tetap disimpan di meta.json, bukan body.typ.
    "riwayat_diterima": "Article History — Received",
    "riwayat_disetujui": "Article History — Accepted",
    "riwayat_terbit": "Article History — Published",
    "email": "Corresponding Author (Email)",
    "sitasi": "To Cite This Article",
}
ID_META = {
    "judul": "m-judul", "penulis": "m-penulis", "afiliasi": "m-afiliasi",
    "abstrak": "m-abstrak", "kata_kunci": "m-kata-kunci",
    "riwayat_diterima": "m-riwayat-diterima",
    "riwayat_disetujui": "m-riwayat-disetujui",
    "riwayat_terbit": "m-riwayat-terbit",
    "email": "m-email",
    "sitasi": "m-sitasi",
}
TIPE_META = {
    "judul": "title", "penulis": "authors", "afiliasi": "affiliation",
    "abstrak": "abstract", "kata_kunci": "keywords",
    "riwayat_diterima": "meta-riwayat",
    "riwayat_disetujui": "meta-riwayat",
    "riwayat_terbit": "meta-riwayat",
    "email": "meta-email",
    "sitasi": "meta-sitasi",
}


def _teks_meta(meta, kunci):
    if kunci == "judul":
        return str(meta.get("judul", ""))
    if kunci == "abstrak":
        return str(meta.get("abstrak", ""))
    if kunci == "kata_kunci":
        return "\n".join(meta.get("kata_kunci", []) or [])
    if kunci == "afiliasi":
        return "\n".join(str(x) for x in (meta.get("afiliasi", []) or []))
    if kunci == "penulis":
        hasil = []
        for p in meta.get("penulis", []) or []:
            nama = str(p.get("nama", ""))
            af = str(p.get("afiliasi", "") or "")
            bintang = "*" if p.get("korespondensi") else ""
            hasil.append("%s%s%s" % (nama, (" (%s)" % af) if af else "", bintang))
        return "\n".join(hasil)
    if kunci.startswith("riwayat_"):
        riwayat = meta.get("riwayat") if isinstance(meta.get("riwayat"), dict) else {}
        return str(riwayat.get(kunci.split("_", 1)[1], "") or "")
    if kunci == "email":
        return str(meta.get("email", "") or "")
    if kunci == "sitasi":
        t = meta.get("terbitan") if isinstance(meta.get("terbitan"), dict) else {}
        return "\n".join([
            "Volume: %s" % (t.get("volume", "") if t.get("volume") is not None else ""),
            "Nomor: %s" % (t.get("nomor", "") if t.get("nomor") is not None else ""),
            "Tahun: %s" % (t.get("tahun", "") if t.get("tahun") is not None else ""),
            "Halaman: %s" % (t.get("halaman_awal", "") if t.get("halaman_awal") is not None else ""),
            "Edisi: %s" % (t.get("edisi", "") or ""),
        ])
    return ""


def _sentence_case(teks):
    from build import sentence_case
    return sentence_case(teks or "")


def _sapaan_penulis(nama):
    from build import apa_penulis
    return apa_penulis(nama)


def _header_penulis(nama):
    from build import header_penulis
    return header_penulis(nama)


def terapkan_meta(dokumen, meta, kunci_diubah=None):
    """Ubah isi meta.json dari blok meta (judul, penulis, afiliasi, abstrak, kata kunci)."""
    for blok in dokumen.get("blocks", []):
        if blok.get("jenis") != "meta":
            continue
        kunci = blok.get("meta_key")
        if kunci_diubah is not None and kunci not in kunci_diubah:
            continue
        teks = blok.get("text", "")
        if kunci == "judul":
            meta["judul"] = teks.strip()
            meta["judul_kalimat"] = _sentence_case(teks.strip())
            kata = teks.split()
            nama = [p.get("nama", "") for p in meta.get("penulis", []) or []]
            meta["header_ganjil"] = _header_penulis(nama) + ", " + " ".join(kata[:7]) + ("..." if len(kata) > 7 else "")
        elif kunci == "abstrak":
            meta["abstrak"] = teks.strip()
        elif kunci == "kata_kunci":
            meta["kata_kunci"] = [x.strip() for x in re.split(r"[;\n]", teks) if x.strip()]
        elif kunci == "afiliasi":
            meta["afiliasi"] = [x.strip() for x in teks.split("\n") if x.strip()]
            if len(meta.get("afiliasi_nomor", []) or []) != len(meta["afiliasi"]):
                meta["afiliasi_nomor"] = [str(i + 1) for i in range(len(meta["afiliasi"]))]
        elif kunci == "penulis":
            penulis = []
            for baris in teks.split("\n"):
                baris = baris.strip()
                if not baris:
                    continue
                m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*(\*)?\s*$", baris)
                if m:
                    penulis.append({"nama": m.group(1).strip(), "afiliasi": m.group(2).strip(),
                                   "korespondensi": bool(m.group(3))})
                else:
                    penulis.append({"nama": baris.rstrip("*").strip(), "afiliasi": "",
                                   "korespondensi": baris.endswith("*")})
            # nomor afiliasi penulis yang kosong: pakai urutan afiliasi bila ada
            for i, p in enumerate(penulis):
                if not p["afiliasi"] and meta.get("afiliasi"):
                    p["afiliasi"] = str(min(i + 1, len(meta["afiliasi"])))
            meta["penulis"] = penulis
            nama = [p["nama"] for p in penulis]
            meta["sitasi_penulis"] = _sapaan_penulis(nama)
            kata = str(meta.get("judul", "")).split()
            meta["header_ganjil"] = _header_penulis(nama) + ", " + " ".join(kata[:7]) + ("..." if len(kata) > 7 else "")
        elif kunci.startswith("riwayat_"):
            riwayat = meta.get("riwayat")
            if not isinstance(riwayat, dict):
                riwayat = {}
            riwayat[kunci.split("_", 1)[1]] = teks.strip().split("\n", 1)[0].strip()
            meta["riwayat"] = riwayat
        elif kunci == "email":
            meta["email"] = teks.strip().split("\n", 1)[0].strip()
        elif kunci == "sitasi":
            terbitan = meta.get("terbitan")
            if not isinstance(terbitan, dict):
                terbitan = {}
            peta = {"volume": "volume", "nomor": "nomor", "tahun": "tahun",
                    "halaman": "halaman_awal", "halaman awal": "halaman_awal",
                    "halaman_awal": "halaman_awal", "edisi": "edisi"}
            for baris in teks.split("\n"):
                m = re.match(r"^\s*([A-Za-z_ ]+?)\s*:\s*(.*?)\s*$", baris)
                if not m:
                    continue
                kunci2 = peta.get(m.group(1).strip().lower())
                if not kunci2:
                    continue
                nilai = m.group(2).strip()
                if not nilai:
                    continue
                if kunci2 == "halaman_awal":
                    if re.match(r"^-?\d+$", nilai):
                        terbitan[kunci2] = int(nilai)
                else:
                    # Template memakai str(terbitan.volume/nomor/tahun); nilai
                    # non-numerik tetap aman selama bukan halaman awal.
                    terbitan[kunci2] = nilai
            meta["terbitan"] = terbitan
    return meta


def _blok_meta(meta):
    hasil = []
    for kunci in ("judul", "penulis", "afiliasi", "abstrak", "kata_kunci",
                  "riwayat_diterima", "riwayat_disetujui", "riwayat_terbit",
                  "email", "sitasi"):
        hasil.append({
            "jenis": "meta", "meta_key": kunci, "id": ID_META[kunci],
            "tipe": TIPE_META[kunci], "judul": LABEL_META[kunci],
            "text": _teks_meta(meta, kunci), "content": _teks_meta(meta, kunci),
            "style": normal_gaya("paragraph", {}),
            "src": {"kind": "meta", "key": kunci, "label": "meta-" + kunci.replace("_", "-")},
        })
    return hasil


def _tipe_teks(jenis):
    return {"heading": "heading", "paragraph": "paragraph", "reference": "reference",
            "list_item": "list_item", "labeled_paragraph": "paragraph", "quote": "quote"}.get(jenis, jenis)


def _label_blok(b):
    j = b.get("jenis")
    if j == "heading":
        return {1: "Judul Bagian", 2: "Subjudul", 3: "Sub-subjudul"}.get(b.get("level", 1), "Judul")
    if j == "paragraph":
        return "Paragraf"
    if j == "reference":
        return "Referensi"
    if j == "list_item":
        return "Butir Daftar"
    if j == "labeled_paragraph":
        return "Paragraf Berlabel"
    if j == "quote":
        return "Kutipan"
    if j == "figure":
        return "Gambar"
    if j == "table":
        return "Tabel"
    if j == "meta":
        return LABEL_META.get(b.get("meta_key"), "Metadata")
    return "Blok"


def _id_baru(jenis, nomor):
    awalan = {"heading": "h", "paragraph": "p", "reference": "ref", "figure": "fig",
              "table": "tbl", "list_item": "li", "labeled_paragraph": "lp", "quote": "q"}.get(jenis, "b")
    return "%s-%03d" % (awalan, nomor)


def _crop_dari_args(args):
    raw_potong = (args or {}).get("potong", "")
    mpot = re.match(r"^\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)$", raw_potong.strip())
    if not mpot:
        return None
    return {"left": float(mpot.group(1)), "top": float(mpot.group(2)),
            "right": float(mpot.group(3)), "bottom": float(mpot.group(4))}


def blok_dari_parse(parsed, lama=None):
    """Beri id + style pada hasil parse, pertahankan id/style dari dokumen lama."""
    lama_pm = {}
    lama_obj = {}
    lama_id = {}
    if lama:
        for b in lama.get("blocks", []):
            if b.get("jenis") == "meta":
                continue
            src = b.get("src", {})
            if src.get("pm"):
                lama_pm[(b.get("jenis"), src["pm"])] = b
            if src.get("nomor"):
                lama_obj[(b.get("jenis"), src["nomor"])] = b
            if b.get("id"):
                lama_id[b["id"]] = b
    hasil = []
    hitung = {}
    for b in parsed:
        j = b["jenis"]
        hitung[j] = hitung.get(j, 0) + 1
        lama_b = None
        if b.get("pm"):
            lama_b = lama_pm.get((j, b["pm"])) or lama_pm.get(("paragraph", b["pm"]))
        if not lama_b and b.get("nomor"):
            lama_b = lama_obj.get((j, b["nomor"]))
        blok = {
            "jenis": j,
            "tipe": _tipe_teks(j) if j != "figure" and j != "table" else j,
            "judul": _label_blok(b),
            "level": b.get("level"),
            "nomor": b.get("nomor"),
            "text": teks_ui_dari_typst(b.get("content", "")),
            "content": b.get("content", ""),
            "caption_raw": b.get("caption_raw", ""),
            "label_raw": b.get("label_raw", ""),
            "panggil": b.get("panggil", {}),
            "table_body": b.get("table_body", ""),
            "penuh": b.get("penuh", False),
            "src": {"kind": "text", "pm": b.get("pm"), "form": j, "prefix": b.get("prefix", "")},
        }
        if b.get("hapus_lanjutan"):
            blok["hapus_lanjutan"] = b["hapus_lanjutan"]
        if j == "figure":
            style = gaya_gambar_bawaan()
            label, isi = label_dari_caption(b.get("caption_raw", ""), b.get("label_raw", ""))
            style["caption"] = (label + (" " + isi if isi else "")).strip()
            style["width_pct"] = round(angka(b.get("panggil", {}).get("args", {}).get("skala", "1")) * 100, 1)
            style["span"] = "page" if b.get("penuh") else "column"
            kp = b.get("panggil", {}).get("args", {}).get("keputusan", '"tempat"').strip('"')
            style["placement"] = {"tempat": "auto", "atas": "top", "bawah": "bottom",
                                  "apung": "float"}.get(kp, "auto")
            crop_args = _crop_dari_args(b.get("panggil", {}).get("args"))
            if crop_args:
                style["crop"] = crop_args
            if lama_b and "spacing_above" in (lama_b.get("style") or {}):
                style["spacing_above"] = lama_b["style"]["spacing_above"]
            lama_st = (lama_b.get("style") or {}) if lama_b else {}
            fz_arg = b.get("panggil", {}).get("args", {}).get("frozen")
            fx_arg = b.get("panggil", {}).get("args", {}).get("freeze-x")
            fy_arg = b.get("panggil", {}).get("args", {}).get("freeze-y")
            sh_arg = b.get("panggil", {}).get("args", {}).get("slot-h")
            if "unlocked_in_freeze" in lama_st:
                style["unlocked_in_freeze"] = bool(lama_st.get("unlocked_in_freeze", False))
            if "frozen" in lama_st and (style.get("unlocked_in_freeze") or fz_arg is None):
                style["frozen"] = bool(lama_st["frozen"])
            elif fz_arg is not None:
                style["frozen"] = fz_arg.strip() == "true"
            elif "frozen" in lama_st:
                style["frozen"] = bool(lama_st["frozen"])
            if fx_arg is not None:
                style["freeze_x"] = round(clamp(angka(fx_arg.replace("pt", "").strip()), -500.0, 500.0), 3)
            elif "freeze_x" in lama_st:
                style["freeze_x"] = lama_st["freeze_x"]
            if fy_arg is not None:
                style["freeze_y"] = round(clamp(angka(fy_arg.replace("pt", "").strip()), -800.0, 800.0), 3)
            elif "freeze_y" in lama_st:
                style["freeze_y"] = lama_st["freeze_y"]
            if sh_arg is not None:
                sh_v = angka(sh_arg.replace("pt", "").strip(), 0.0)
                style["freeze_slot_h"] = round(clamp(sh_v, 11.0, 760.0), 3) if sh_v > 1.0 else 0.0
            elif "freeze_slot_h" in lama_st:
                style["freeze_slot_h"] = lama_st["freeze_slot_h"]
            blok["src"] = {"kind": "object", "object": "gambar", "nomor": b.get("nomor"),
                           "path": b.get("panggil", {}).get("path", ""),
                           "args": b.get("panggil", {}).get("args", {})}
            blok["text"] = style["caption"]
            blok["style"] = normal_gaya("figure", style)
        elif j == "table":
            style = gaya_tabel_bawaan()
            label, isi = label_dari_caption(b.get("caption_raw", ""), b.get("label_raw", ""))
            style["caption"] = (label + (" " + isi if isi else "")).strip()
            style["span"] = "page" if b.get("penuh") else "column"
            style["font_size"] = UKURAN_TABEL_BAWAAN
            crop_args = _crop_dari_args(b.get("panggil", {}).get("args"))
            if crop_args:
                style["crop"] = crop_args
            if lama_b and "spacing_above" in (lama_b.get("style") or {}):
                style["spacing_above"] = lama_b["style"]["spacing_above"]
            lama_st = (lama_b.get("style") or {}) if lama_b else {}
            fz_arg = b.get("panggil", {}).get("args", {}).get("frozen")
            fx_arg = b.get("panggil", {}).get("args", {}).get("freeze-x")
            fy_arg = b.get("panggil", {}).get("args", {}).get("freeze-y")
            sh_arg = b.get("panggil", {}).get("args", {}).get("slot-h")
            if "unlocked_in_freeze" in lama_st:
                style["unlocked_in_freeze"] = bool(lama_st.get("unlocked_in_freeze", False))
            if "frozen" in lama_st and (style.get("unlocked_in_freeze") or fz_arg is None):
                style["frozen"] = bool(lama_st["frozen"])
            elif fz_arg is not None:
                style["frozen"] = fz_arg.strip() == "true"
            elif "frozen" in lama_st:
                style["frozen"] = bool(lama_st["frozen"])
            if fx_arg is not None:
                style["freeze_x"] = round(clamp(angka(fx_arg.replace("pt", "").strip()), -500.0, 500.0), 3)
            elif "freeze_x" in lama_st:
                style["freeze_x"] = lama_st["freeze_x"]
            if fy_arg is not None:
                style["freeze_y"] = round(clamp(angka(fy_arg.replace("pt", "").strip()), -800.0, 800.0), 3)
            elif "freeze_y" in lama_st:
                style["freeze_y"] = lama_st["freeze_y"]
            if sh_arg is not None:
                sh_v = angka(sh_arg.replace("pt", "").strip(), 0.0)
                style["freeze_slot_h"] = round(clamp(sh_v, 11.0, 760.0), 3) if sh_v > 1.0 else 0.0
            elif "freeze_slot_h" in lama_st:
                style["freeze_slot_h"] = lama_st["freeze_slot_h"]
            fungsi_t = (b.get("panggil", {}).get("fungsi", "tabel") or "tabel").lstrip("#").rstrip("(")
            blok["src"] = {"kind": "object", "object": "tabel", "nomor": b.get("nomor"),
                           "fungsi": fungsi_t,
                           "args": b.get("panggil", {}).get("args", {})}
            blok["text"] = style["caption"]
            blok["style"] = normal_gaya("table", style)
            blok["cells"] = [{"r": s["r"], "c": s["c"], "text": teks_ui_dari_typst(s["content"]),
                              "header": s.get("header", False), "span_r": s.get("span_r", 1),
                              "span_c": s.get("span_c", 1)} for s in sel_table(b.get("table_body", ""))]
        else:
            lama_st = (lama_b.get("style") or {}) if lama_b else {}
            style_teks = dict(lama_st)
            if b.get("break_before") and "break_before" not in lama_st:
                style_teks["break_before"] = b["break_before"]
            if "unlocked_in_freeze" in lama_st:
                style_teks["unlocked_in_freeze"] = bool(lama_st.get("unlocked_in_freeze", False))
            if "frozen" in b and not (style_teks.get("unlocked_in_freeze") and "frozen" in lama_st):
                style_teks["frozen"] = b["frozen"]
            if "freeze_x" in b:
                style_teks["freeze_x"] = b["freeze_x"]
            if "freeze_y" in b:
                style_teks["freeze_y"] = b["freeze_y"]
            for fk in ("freeze_w", "freeze_slot_h", "tracking", "leading"):
                if fk in b and fk not in lama_st:
                    style_teks[fk] = b[fk]
            if b.get("no_indent"):
                style_teks["no_indent"] = True
            blok["style"] = normal_gaya(blok["tipe"], style_teks)
        if lama_b:
            blok["id"] = lama_b.get("id")
            blok["_lama"] = True
            if b.get("pm") and lama_b.get("src", {}).get("pm") == b.get("pm"):
                lama_st = lama_b.get("style") or {}
                style_lama = dict(lama_st)
                if b.get("break_before") and "break_before" not in lama_st:
                    style_lama["break_before"] = b["break_before"]
                if "unlocked_in_freeze" in lama_st:
                    style_lama["unlocked_in_freeze"] = bool(lama_st.get("unlocked_in_freeze", False))
                if "frozen" in b and not (style_lama.get("unlocked_in_freeze") and "frozen" in lama_st):
                    style_lama["frozen"] = b["frozen"]
                if "freeze_x" in b:
                    style_lama["freeze_x"] = b["freeze_x"]
                if "freeze_y" in b:
                    style_lama["freeze_y"] = b["freeze_y"]
                for fk in ("freeze_w", "freeze_slot_h", "tracking", "leading"):
                    if fk in b and fk not in lama_st:
                        style_lama[fk] = b[fk]
                if b.get("no_indent") and "no_indent" not in lama_st:
                    style_lama["no_indent"] = True
                blok["style"] = normal_gaya(blok["tipe"], style_lama)
        if not blok.get("id"):
            blok["id"] = _id_baru(j, hitung[j])
        hasil.append(blok)
    # Jamin keunikan id: id lama diprioritaskan; blok baru/duplikat mendapat nomor berikutnya.
    id_lama = {b["id"] for b in hasil if b.get("_lama")}
    dipakai = set()
    hitung_ulang = {}

    def id_unik(jenis):
        n = hitung_ulang.get(jenis, 0)
        while True:
            n += 1
            calon = _id_baru(jenis, n)
            if calon not in dipakai and calon not in id_lama:
                hitung_ulang[jenis] = n
                return calon

    for b in hasil:
        if b.get("_lama"):
            if b["id"] in dipakai:
                b["id"] = id_unik(b["jenis"])
        else:
            if b["id"] in dipakai or b["id"] in id_lama:
                b["id"] = id_unik(b["jenis"])
        dipakai.add(b["id"])
        b.pop("_lama", None)
    return hasil


def migrasi(folder, lama=None):
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    parsed = parse_body(body)
    blok = _blok_meta(meta) + blok_dari_parse(parsed, lama)
    dok = {"versi": 3, "artikel": os.path.basename(folder), "dibuat": sekarang(),
           "body_hash": hash_teks(body), "blocks": blok}
    return dok, body, meta


def muat_dokumen(folder, tulis=True):
    """Muat dokumen.json; migrasi/merge otomatis bila body berubah."""
    path = os.path.join(folder, NAMA_DOKUMEN)
    body = baca_teks(os.path.join(folder, "body.typ"))
    if "#pm(" not in body:
        body_penanda = tambah_penanda_body(body)
        if body_penanda != body:
            body = body_penanda
            if tulis:
                with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
                    f.write(body)
    body_objek = tambah_nomor_objek_body(body)
    if body_objek != body:
        body = body_objek
        if tulis:
            with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
                f.write(body)
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    lama = baca_json(path)
    if lama and lama.get("versi") == 3 and lama.get("body_hash") == hash_teks(body):
        blok = lama.get("blocks", [])
        punya_meta = {b.get("meta_key") for b in blok if b.get("jenis") == "meta"}
        if set(ID_META) - punya_meta:
            blok = _blok_meta(meta) + [b for b in blok if b.get("jenis") != "meta"]
            lama["blocks"] = blok
            if tulis:
                tulis_json(path, lama)
        return lama
    dok, _, _ = migrasi(folder, lama)
    if tulis:
        tulis_json(path, dok)
        _pastikan_snapshot(folder, dok, body, meta, "Migrasi blok awal", paksa=True)
    return dok


def simpan_dokumen(folder, dok):
    dok["body_hash"] = hash_teks(baca_teks(os.path.join(folder, "body.typ")))
    tulis_json(os.path.join(folder, NAMA_DOKUMEN), dok)


# ======================================================================
#  Render body.typ
# ======================================================================
def _args_gaya(style):
    bagian = []
    if abs(angka(style.get("tracking"))) > 0.0005:
        bagian.append("tracking: %s" % fmt_pt(style["tracking"]))
    if abs(angka(style.get("leading"))) > 0.0005:
        bagian.append("leading: %s" % fmt_pt(style["leading"]))
    if style.get("keep_with_next"):
        bagian.append("jaga: true")
    return ", ".join(bagian)


def _penanda_akhir(blok):
    return '#metadata("akhir")#label("blk-akhir-%s")' % blok["id"]


def _bungkus_gaya_teks(blok, isi):
    """Terapkan tracking/leading dengan set-rule standar Typst.

    Template produksi terbaru tidak lagi menyediakan fungsi ``gaya-teks``;
    set-rule di dalam blok konten memberi hasil tipografi yang sama tanpa
    bergantung pada helper template.
    """
    st = blok.get("style", {}) or {}
    aturan = []
    if abs(angka(st.get("tracking"))) > 0.0005:
        aturan.append("#set text(tracking: %s)" % fmt_pt(st["tracking"]))
    if abs(angka(st.get("leading"))) > 0.0005:
        aturan.append("#set par(leading: %s)" % fmt_pt(st["leading"]))
    if not aturan:
        return isi
    # Satu baris agar render_body yang berorientasi baris tidak menggandakan
    # pembungkus pada penyimpanan berikutnya. Set-rule dipisah titik koma.
    return "#[%s; %s]" % ("; ".join(aturan), isi)


def render_teks(blok, lewatkan_gaya=False):
    src = blok.get("src", {})
    isi = _RE_PE_PF.sub("", blok.get("content", "")).rstrip()
    if not lewatkan_gaya:
        isi = _bungkus_gaya_teks(blok, isi)
    # Spasi sebelum penanda mencegah auto-link Typst menelan metadata akhir
    # pada teks yang berakhir dengan URL tanpa pemisah; #pe(pm) wajib untuk QA ketat.
    pe_tag = ("#pe(%s)" % src.get("pm")) if src.get("pm") else ""
    isi += (" " + pe_tag if pe_tag else "") + " " + _penanda_akhir(blok)
    src = blok.get("src", {})
    j = src.get("form") or blok.get("jenis")
    if j == "heading":
        return "%s #pm(%s);%s" % ("=" * int(blok.get("level") or 1), src.get("pm"), isi)
    if j == "paragraph":
        return "#pm(%s);%s" % (src.get("pm"), isi)
    return "%s#pm(%s);%s]" % (src.get("prefix", ""), src.get("pm"), isi)


def _arg_raw(b, kunci, bawaan=""):
    return b.get("src", {}).get("args", {}).get(kunci, bawaan)


def _fmt_potong(style):
    crop = style.get("crop")
    if isinstance(crop, dict):
        cl = angka(crop.get("left"), 0.0)
        ct = angka(crop.get("top"), 0.0)
        cr = angka(crop.get("right"), 0.0)
        cb = angka(crop.get("bottom"), 0.0)
        if cl >= 0.005 or ct >= 0.005 or cr >= 0.005 or cb >= 0.005:
            return "potong: (%.4f, %.4f, %.4f, %.4f)" % (cl, ct, cr, cb)
    return ""


def render_gambar(blok):
    style = normal_gaya("figure", blok.get("style"))
    src = blok.get("src", {})
    bagian = [src.get("path") or '"???"']
    for kunci, label_arg in (("lebar-word", "lebar-word"), ("lebar-tampil", "lebar-tampil")):
        nilai = _arg_raw(blok, kunci)
        if nilai:
            bagian.append("%s: %s" % (label_arg, nilai))
    if _arg_raw(blok, "di-tempat").strip() == "true":
        bagian.append("di-tempat: true")
    label, isi = label_dari_caption(style.get("caption", ""), "")
    if not label:
        label = blok.get("label_raw", "") or ""
    if label and not label.endswith("."):
        label += "."
    bagian.append("penuh: %s" % ("true" if style.get("span") == "page" else "false"))
    keputusan = {"auto": "tempat", "top": "atas", "bottom": "bawah",
                 "none": "tempat", "float": "apung"}.get(style.get("placement"), "tempat")
    bagian.append('keputusan: "%s"' % keputusan)
    skala = angka(style.get("width_pct"), 100.0) / 100.0
    if abs(skala - 1.0) >= 0.0005:
        bagian.append("skala: %.3f" % skala)
    bagian.append("nomor: %s" % src.get("nomor"))
    pot = _fmt_potong(style)
    if pot:
        bagian.append(pot)
    bagian.append("label: [%s]" % _escape_typst_teks(label))
    bagian.append("isi: [%s]" % typst_dari_teks_ui(isi))
    fx = angka(style.get("freeze_x"))
    fy = angka(style.get("freeze_y"))
    slot_h = angka(style.get("freeze_slot_h"))
    is_beku = bool(style.get("frozen")) or bool(style.get("unlocked_in_freeze")) or abs(fx) > 0.05 or abs(fy) > 0.05 or slot_h > 1.0
    if is_beku:
        bagian.append("frozen: true")
        bagian.append("freeze-x: %s" % fmt_pt(fx))
        bagian.append("freeze-y: %s" % fmt_pt(fy))
        if slot_h > 1.0:
            bagian.append("slot-h: %s" % fmt_pt(slot_h))
    # Template produksi terbaru tidak menerima jarak-atas/jarak-bawah pada
    # #gambar(); jarak diatur oleh mesin tata letak template.
    catatan = _arg_raw(blok, "catatan")
    if catatan:
        bagian.append("catatan: [%s]" % lepas_kurung(catatan))
    return "#gambar(%s)" % ", ".join(bagian)


def render_tabel(blok, table_body, multi_seg_ids=None):
    style = normal_gaya("table", blok.get("style"))
    src = blok.get("src", {})
    fungsi = (src.get("fungsi") or "tabel").lstrip("#").rstrip("(")
    args = src.get("args", {})
    label, isi = label_dari_caption(style.get("caption", ""), "")
    if not label:
        label = blok.get("label_raw", "") or ""
    if label and not label.endswith("."):
        label += "."
    label_t = _escape_typst_teks(label)
    isi_t = typst_dari_teks_ui(isi)
    penuh = "true" if style.get("span") == "page" else "false"
    kepala = args.get("kepala", "1") or "1"
    bagian = []
    if fungsi != "tabel-panjang":
        bagian.append("jumlah-kolom: %s" % (args.get("jumlah-kolom", "1") or "1"))
        bagian.append("penuh: %s" % penuh)
    bagian.append("kepala: %s" % kepala)
    if args.get("garis-baris", "").strip() == "false":
        bagian.append("garis-baris: false")
    if args.get("di-tempat", "").strip() == "true":
        bagian.append("di-tempat: true")
    # Template produksi terbaru mengatur ukuran/cell-pad di dalam template;
    # nilai mikro diterapkan lewat prefiks set-rule di dalam badan tabel.
    if fungsi != "tabel-panjang":
        bagian.append("keputusan: %s" % args.get("keputusan", '"tempat"'))
        if src.get("nomor"):
            bagian.append("nomor: %s" % src.get("nomor"))
        elif args.get("nomor"):
            bagian.append("nomor: %s" % args.get("nomor"))
    pot = _fmt_potong(style)
    if pot:
        bagian.append(pot)
    bagian.append("label: [%s]" % label_t)
    bagian.append("isi: [%s]" % isi_t)
    fx = angka(style.get("freeze_x"))
    fy = angka(style.get("freeze_y"))
    slot_h = angka(style.get("freeze_slot_h"))
    is_multi_seg_unmoved = (blok.get("id") in (multi_seg_ids or set()) and abs(fx) <= 0.05 and abs(fy) <= 0.05)
    is_beku = bool(style.get("frozen")) or bool(style.get("unlocked_in_freeze")) or abs(fx) > 0.05 or abs(fy) > 0.05 or (slot_h > 1.0 and not is_multi_seg_unmoved)
    if is_beku:
        bagian.append("frozen: true")
        bagian.append("freeze-x: %s" % fmt_pt(fx))
        bagian.append("freeze-y: %s" % fmt_pt(fy))
        if slot_h > 1.0 and not is_multi_seg_unmoved:
            bagian.append("slot-h: %s" % fmt_pt(slot_h))
        if is_multi_seg_unmoved:
            bagian.append("boleh-pecah: true")
    return "#%s(%s)[" % (fungsi, ", ".join(bagian))


def _prefiks_gaya_tabel(blok):
    """Set-rule mikro tabel yang tidak lagi menjadi parameter #tabel template."""
    st = normal_gaya("table", blok.get("style"))
    aturan = []
    fs = angka(st.get("font_size"), UKURAN_TABEL_BAWAAN)
    if abs(fs - UKURAN_TABEL_BAWAAN) > 0.0005:
        aturan.append("#set text(%s)" % fmt_pt(fs))
    pad = angka(st.get("cell_pad"), 4.0)
    if abs(pad - 4.0) > 0.0005:
        aturan.append("#set table(inset: (x: %s, y: 2.5pt))" % fmt_pt(pad))
    return ("\n".join(aturan) + "\n") if aturan else ""


def _baris_objek(body, jenis, nomor):
    """(awal, akhir) blok teks objek dengan nomor tertentu; akhir eksklusif."""
    if jenis == "gambar":
        for m in re.finditer(r"^#gambar\(.*?nomor:\s*%s\b" % re.escape(str(nomor)), body, flags=re.M):
            a = m.start()
            b = body.find("\n", a)
            return a, (b + 1 if b >= 0 else len(body))
        return None
    for m in re.finditer(r"^#(?:tabel|tabel-panjang)\(", body, flags=re.M):
        sisa = body[m.start():]
        kp = sisa.find("(")
        kt = cari_penutup(sisa, kp, "(", ")")
        if kt < 0:
            continue
        kb = sisa.find("[", kt)
        if kb < 0:
            continue
        ktutup = cari_penutup(sisa, kb, "[", "]")
        if ktutup < 0:
            continue
        args = sisa[kp + 1:kt]
        if re.search(r"nomor:\s*%s\b" % re.escape(str(nomor)), args):
            return m.start(), m.start() + ktutup + 1
    return None


_RE_KOLOM_BUKA = re.compile(r"^#kolom(?:-lanjut)?(?:\([^\n]*?\))?\[")
_RE_PM_SEMUA = re.compile(r"#pm\(\d+\);")
_RE_TABEL_BARIS = re.compile(r"^#(?:tabel|tabel-panjang)\(")


def _pola_pm_baris(n):
    """Pola baris sumber blok teks (sama dengan pola_pm di api.php)."""
    return re.compile(
        r"^(?:#beku\([^)]*\)\[\s*)?(?:=+ |#referensi(?:\([^)]*\))?\[|#butir\([^)]*\)\[|#wawancara\["
        r"|#label-gantung\([^)]*\)\[)?#pm\(%d\);" % int(n))


def _rentang_akhir_pernyataan(baris, awal):
    """Indeks baris terakhir pernyataan Typst yang dimulai pada baris ``awal``.

    Hanya kurung siku yang dihitung (kurung biasa di dalam argumen selalu
    berpasangan); string dan komentar diabaikan agar ``]`` di dalamnya tidak
    memotong pernyataan. Dipakai untuk blok tabel dan pembungkus ``#[...]``.
    """
    kedalaman = 0
    for i in range(awal, len(baris)):
        teks = baris[i]
        dalam_string = False
        j = 0
        while j < len(teks):
            c = teks[j]
            if dalam_string:
                if c == "\\":
                    j += 1
                elif c == '"':
                    dalam_string = False
            elif c == '"':
                dalam_string = True
            elif c == "/" and j + 1 < len(teks) and teks[j + 1] == "/":
                break
            elif c == "\\":
                j += 1
            elif c == "[":
                kedalaman += 1
            elif c == "]" and kedalaman > 0:
                kedalaman -= 1
            j += 1
        if kedalaman == 0:
            return i
    return len(baris) - 1


def _rentang_kolom(baris):
    """Daftar (baris_buka, baris_tutup) untuk setiap pembungkus #kolom[...]."""
    hasil = []
    for i, teks in enumerate(baris):
        if _RE_KOLOM_BUKA.match(teks):
            hasil.append((i, _rentang_akhir_pernyataan(baris, i)))
    return hasil


def _perluas_wrapper_inline(baris, awal, akhir):
    """Ikutkan pembungkus #[#set text(...) ... ] atau #beku(...) yang hanya membungkus blok ini."""
    j = awal - 1
    while j >= 0 and not baris[j].strip():
        j -= 1
    if j < 0 or not (baris[j].lstrip().startswith("#[") or baris[j].lstrip().startswith("#beku(")):
        return awal, akhir
    akhir_w = _rentang_akhir_pernyataan(baris, j)
    if akhir_w < akhir:
        return awal, akhir
    jumlah = sum(len(_RE_PM_SEMUA.findall(baris[k])) for k in range(j, akhir_w + 1))
    if jumlah == 1:
        return j, akhir_w
    return awal, akhir


def _rentang_blok_body(baris, blok):
    """(baris_awal, baris_akhir inklusif) potongan body.typ untuk satu blok."""
    jenis = blok.get("jenis", "")
    src = blok.get("src", {}) or {}
    if jenis in ("figure", "table"):
        nomor = src.get("nomor", blok.get("nomor"))
        if nomor is None:
            return None
        if jenis == "figure":
            pola = re.compile(r"^#gambar\(.*?nomor:\s*%s\b" % re.escape(str(nomor)))
            for i, teks in enumerate(baris):
                if pola.match(teks):
                    # Gambar selalu satu baris; jangan menelan blok berikutnya bila
                    # ada kurung siku literal yang tidak seimbang.
                    return i, i
            return None
        for i, teks in enumerate(baris):
            if not _RE_TABEL_BARIS.match(teks):
                continue
            akhir = _rentang_akhir_pernyataan(baris, i)
            isi = "\n".join(baris[i:akhir + 1])
            if re.search(r"nomor:\s*%s\b" % re.escape(str(nomor)), isi):
                return i, akhir
        return None
    pm = src.get("pm")
    if not pm:
        return None
    pola = _pola_pm_baris(pm)
    for i, teks in enumerate(baris):
        if pola.match(teks):
            akhir = _rentang_akhir_pernyataan(baris, i)
            # Bila pemindaian menelan blok lain (mis. kurung siku literal di teks),
            # batasi potongan pada baris blok itu saja.
            isi = "\n".join(baris[i:akhir + 1])
            if sum(len(_RE_PM_SEMUA.findall(baris[k])) for k in range(i, akhir + 1)) > 1 \
                    or re.search(r"^#(?:gambar|tabel|tabel-panjang)\(", isi, flags=re.M):
                akhir = i
            return _perluas_wrapper_inline(baris, i, akhir)
    return None


def _bersihkan_potongan(potongan):
    """Buang baris kosong di ujung potongan agar sisipan tetap rapi."""
    while potongan and not potongan[0].strip():
        potongan.pop(0)
    while potongan and not potongan[-1].strip():
        potongan.pop()
    return potongan


def _susun_ulang_body(body, dok, ids, target_id, posisi, placement=None):
    """Pindahkan potongan body.typ untuk ``ids`` ke sebelum/sesudah target.

    Pembungkus #kolom[...] dipertahankan; bila target berada di luar segmen
    (objek selebar halaman), blok teks yang dipindah dibungkus #kolom baru agar
    tetap mengalir dua kolom.
    """
    baris = body.split("\n")
    blok_map = {b.get("id"): b for b in dok.get("blocks", [])}
    rentang = {}
    for bid in list(ids) + [target_id]:
        r = _rentang_blok_body(baris, blok_map[bid])
        if not r:
            return None, None, "Batas sumber blok %s tidak ditemukan di body.typ" % bid
        rentang[bid] = r
    # Blok yang dipindah tidak boleh tumpang tindih dengan blok lain.
    dipindah_r = sorted(rentang[bid] for bid in ids)
    for i in range(1, len(dipindah_r)):
        if dipindah_r[i][0] <= dipindah_r[i - 1][1]:
            return None, None, "Potongan blok yang dipilih saling tumpang tindih"
    ts, te = rentang[target_id]
    for bid in ids:
        a, b = rentang[bid]
        if a <= ts <= b or a <= te <= b:
            return None, None, "Blok tujuan berada di dalam blok yang dipindah"

    # Wrapper tempat target berada (None = target top-level, mis. gambar lebar).
    kolom = _rentang_kolom(baris)
    wrapper_target = next((k for k, (oa, ob) in enumerate(kolom) if oa < ts <= ob), None)

    titik = ts if posisi == "before" else te + 1
    potongan = [_bersihkan_potongan(list(baris[rentang[bid][0]:rentang[bid][1] + 1])) for bid in ids]
    hasil = []
    i = 0
    for a, b in dipindah_r:
        hasil.extend(baris[i:a])
        i = b + 1
    hasil.extend(baris[i:])
    geser = sum(max(0, min(b + 1, titik) - a) for a, b in dipindah_r)
    titik2 = max(0, titik - geser)

    semua_objek = all(blok_map[bid].get("jenis") in ("figure", "table") for bid in ids)
    # Hanya dokumen dua kolom yang punya pembungkus #kolom; dokumen satu kolom
    # cukup disisipi apa adanya agar tidak berubah menjadi dua kolom.
    bungkus_kolom = wrapper_target is None and not semua_objek and bool(kolom)
    sisip = []
    if bungkus_kolom:
        sisip.append("#kolom[")
    for n, pot in enumerate(potongan):
        if n:
            sisip.append("")
        sisip.extend(pot)
    if bungkus_kolom:
        sisip.append("]")
    if titik2 > 0 and hasil[titik2 - 1].strip() and sisip and sisip[0].strip():
        sisip.insert(0, "")
    if titik2 < len(hasil) and hasil[titik2].strip() and sisip and sisip[-1].strip():
        sisip.append("")
    baris_baru = hasil[:titik2] + sisip + hasil[titik2:]
    body_baru = "\n".join(baris_baru)
    return body_baru, blok_map, None


def _pindah_memori(dok, body, block_ids, target_id, posisi, placement=None):
    """Terapkan satu operasi pindah ke ``dok``/``body`` di memori.

    Tidak menulis berkas dan tidak mengompilasi. Mengembalikan
    ``(ok, body_baru, ids, galat, berubah)``. ``dok`` dimutasi saat berhasil.
    """
    posisi = str(posisi or "").lower()
    if posisi not in ("before", "after", "sebelum", "sesudah"):
        return False, body, [], "Posisi harus before/after", False
    posisi = "before" if posisi in ("before", "sebelum") else "after"
    placement = str(placement or "").lower()
    if placement not in ("", "top", "bottom", "float", "atas", "bawah", "apung"):
        return False, body, [], "Placement tidak sah", False
    placement = {"atas": "top", "bawah": "bottom", "apung": "float"}.get(placement, placement) or None

    ids = []
    for raw in block_ids if isinstance(block_ids, (list, tuple)) else []:
        bid = str(raw)
        if bid and bid not in ids:
            ids.append(bid)
    if not ids:
        return False, body, [], "Tidak ada blok yang dipindah", False
    if len(ids) > 200:
        return False, body, [], "Terlalu banyak blok dalam satu pemindahan (maks. 200)", False
    target_id = str(target_id or "")
    if not target_id:
        return False, body, [], "Blok tujuan tidak diberikan", False
    if target_id in ids:
        return False, body, [], "Blok tujuan tidak boleh termasuk blok yang dipindah", False

    blok_map = {b.get("id"): b for b in dok.get("blocks", [])}
    for bid in ids + [target_id]:
        blok = blok_map.get(bid)
        if not blok:
            return False, body, [], "Blok %s tidak ditemukan" % bid, False
        if blok.get("jenis") == "meta":
            return False, body, [], "Blok metadata %s tidak dapat dipindah" % bid, False

    urutan = {b.get("id"): i for i, b in enumerate(dok.get("blocks", []))}
    ids.sort(key=lambda x: urutan.get(x, 1 << 30))
    ids_set = set(ids)
    sisa_id = [b["id"] for b in dok["blocks"] if b.get("id") not in ids_set]
    idx_target = next((i for i, bid in enumerate(sisa_id) if bid == target_id), None)
    if idx_target is None:
        return False, body, [], "Blok tujuan tidak ditemukan", False
    idx_sisip = idx_target + (1 if posisi == "after" else 0)
    urutan_baru = sisa_id[:idx_sisip] + ids + sisa_id[idx_sisip:]
    urutan_lama = [b["id"] for b in dok["blocks"]]
    ubah_penempatan = False
    if placement:
        for bid in ids:
            b = blok_map[bid]
            if b.get("jenis") == "figure":
                if normal_gaya("figure", b.get("style")).get("placement") != placement:
                    ubah_penempatan = True
            elif b.get("jenis") == "table":
                args = b.get("src", {}).get("args", {}) or {}
                nilai = ('"atas"' if placement == "top" else
                         '"bawah"' if placement == "bottom" else '"apung"')
                if args.get("keputusan") != nilai:
                    ubah_penempatan = True
    if urutan_baru == urutan_lama and not ubah_penempatan:
        return True, body, ids, None, False

    body_baru, _, galat = _susun_ulang_body(body, dok, ids, target_id, posisi, placement)
    if galat:
        return False, body, [], galat, False

    dipindah = [blok_map[bid] for bid in ids]
    sisa = [b for b in dok["blocks"] if b.get("id") not in ids_set]
    idx_target = next(i for i, b in enumerate(sisa) if b.get("id") == target_id)
    idx_sisip = idx_target + (1 if posisi == "after" else 0)
    sisa[idx_sisip:idx_sisip] = dipindah
    dok["blocks"] = sisa

    if placement:
        for b in dipindah:
            if b.get("jenis") == "figure":
                st = normal_gaya("figure", b.get("style"))
                st["placement"] = placement
                b["style"] = st
            elif b.get("jenis") == "table":
                args = (b.setdefault("src", {}) or {}).setdefault("args", {})
                args["keputusan"] = ('"atas"' if placement == "top" else
                                     '"bawah"' if placement == "bottom" else '"apung"')
    return True, body_baru, ids, None, True


def pindahkan_blok(folder, block_ids, target_id, posisi, placement=None):
    """Pindahkan satu/beberapa blok ke sebelum/sesudah blok target.

    Urutan array dokumen, body.typ, dan posisi layout_map disinkronkan dalam
    satu kompilasi SVG cepat (tanpa PDF/QA berat). Snapshot riwayat disimpan
    sebelum perubahan sehingga Ctrl+Z dapat mengembalikannya.
    """
    folder = os.path.abspath(folder)
    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    peta_lama = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}
    _simpan_snapshot(folder, dok, body, meta, "Sebelum pindah blok")
    ok, body_baru, ids, galat, berubah = _pindah_memori(
        dok, body, block_ids, target_id, posisi, placement)
    if not ok:
        return {"ok": False, "galat": galat}
    if not berubah:
        hasil = ruang_kerja(folder, paksa_kompilasi=False)
        hasil.update({"halaman_berubah": [], "dipindah": [], "diubah": False})
        return hasil

    body_final = render_body(body_baru, dok)
    k = _terapkan_dan_kompilasi(folder, dok, body_final, meta)
    if not k.get("ok"):
        return k
    dok = muat_dokumen(folder)
    label = "Pindah %d blok" % len(ids) if len(ids) > 1 else "Pindah %s" % ids[0]
    _simpan_snapshot(folder, dok, baca_teks(os.path.join(folder, "body.typ")),
                     baca_json(os.path.join(folder, "meta.json"), {}), label)
    hasil = ruang_kerja(folder, paksa_kompilasi=False)
    hasil["halaman_berubah"] = _halaman_berubah(
        peta_lama, baca_json(os.path.join(folder, "layout_map.json"), {}) or {})
    hasil["dipindah"] = ids
    hasil["diubah"] = True
    return hasil


def _render_baris_beku_kol(blk, baris_baru, fx, fy, no_indent, seg_pieces):
    """Render paragraf beku lintas-kolom ke dalam #beku-kol(fx, fy)[seg0][seg1]..."""
    pm = (blk.get("src") or {}).get("pm")
    akhir_tag = _penanda_akhir(blk)
    gaya_pre = ""
    mg = re.match(r"^(#gaya-teks\([^)]*\)\[)", baris_baru)
    if mg and baris_baru.endswith("]"):
        gaya_pre = mg.group(1)
    potongan = []
    last_i = len(seg_pieces) - 1
    for i, seg_txt in enumerate(seg_pieces):
        seg_body = seg_txt.strip()
        if i == 0:
            awal_tag = ("#pm(%s);" % pm) if pm else ""
            tutup_tag = ("#pe(%s)" % pm) if pm else ""
            isi_i = "%s%s%s#linebreak(justify: true)" % (awal_tag, seg_body, tutup_tag)
        else:
            awal_tag = ("#pf(%s, %d);" % (pm, i)) if pm else ""
            tutup_tag = ("#pe(%s, bagian: %d)" % (pm, i)) if pm else ""
            if i < last_i:
                isi_i = "%s%s%s#linebreak(justify: true)" % (awal_tag, seg_body, tutup_tag)
            else:
                isi_i = "%s%s%s %s" % (awal_tag, seg_body, tutup_tag, akhir_tag)
        if gaya_pre:
            isi_i = "%s%s]" % (gaya_pre, isi_i)
        potongan.append("[%s]" % isi_i)
    suffix = ", tanpa-inden: true" if no_indent else ""
    return "#beku-kol(%s, %s%s)%s" % (fmt_pt(fx), fmt_pt(fy), suffix, "".join(potongan))


def _baris_blok_teks(blk, multi_seg_ids=None, izinkan_pecah=True):
    """Render satu blok teks beserta direktif spasi/break dan pembungkus #beku."""
    hasil = []
    st = blk.get("style", {}) or {}
    fx = angka(st.get("freeze_x"))
    fy = angka(st.get("freeze_y"))
    fw = angka(st.get("freeze_w"))
    slot_h = angka(st.get("freeze_slot_h"))
    trk = angka(st.get("tracking"))
    lead = angka(st.get("leading"))
    has_freeze_box = abs(fx) > 0.05 or abs(fy) > 0.05 or fw > 1.0 or slot_h > 1.0
    is_beku = bool(st.get("frozen")) or bool(st.get("unlocked_in_freeze")) or has_freeze_box
    no_indent = bool(st.get("no_indent"))
    seg_pieces = (blk.get("src") or {}).get("seg_pieces")
    use_beku_kol = (is_beku and isinstance(seg_pieces, list) and len(seg_pieces) >= 2
                    and blk.get("jenis") == "paragraph"
                    and (abs(fx) > 0.05 or abs(fy) > 0.05)
                    and fw <= 1.0 and slot_h <= 1.0
                    and st.get("break_before", "none") == "none")
    baris_baru = render_teks(blk, lewatkan_gaya=has_freeze_box and not use_beku_kol)
    if use_beku_kol:
        baris_baru = _render_baris_beku_kol(blk, baris_baru, fx, fy, no_indent, seg_pieces)
    elif is_beku:
        extra_args = []
        if izinkan_pecah and blk.get("id") in (multi_seg_ids or set()) and not has_freeze_box:
            extra_args.append("boleh-pecah: true")
        if no_indent:
            extra_args.append("tanpa-inden: true")
        if has_freeze_box:
            if fw > 1.0:
                extra_args.append("freeze-w: %s" % fmt_pt(fw))
            if slot_h > 1.0:
                extra_args.append("slot-h: %s" % fmt_pt(slot_h))
            if abs(trk) > 0.0005:
                extra_args.append("freeze-tracking: %s" % fmt_pt(trk))
            if abs(lead) > 0.0005:
                extra_args.append("freeze-leading: %s" % fmt_pt(lead))
        suffix = (", " + ", ".join(extra_args)) if extra_args else ""
        baris_baru = "#beku(%s, %s%s)[%s]" % (fmt_pt(fx), fmt_pt(fy), suffix, baris_baru)
    atas = angka(st.get("spacing_above"))
    bawah = angka(st.get("spacing_below"))
    pindah = st.get("break_before", "none")
    if abs(atas) > 0.0005:
        hasil.append("#v(%s) // ed:%s" % (fmt_pt(atas), blk["id"]))
    if pindah == "column":
        hasil.append("#colbreak() // ed:%s" % blk["id"])
    elif pindah == "page":
        hasil.append("#pagebreak(weak: true) // ed:%s" % blk["id"])
    hasil.append(baris_baru)
    if abs(bawah) > 0.0005:
        hasil.append("#v(%s, weak: true) // ed:%s" % (fmt_pt(bawah), blk["id"]))
    return hasil


def render_body(body_lama, dok, multi_seg_ids=None):
    """Tulis ulang body.typ: ganti baris blok, buang lanjutan lama, tambah blok baru."""
    blok = dok.get("blocks", [])
    peta_pm = {b["src"].get("pm"): b for b in blok
               if b.get("jenis") not in ("meta", "figure", "table") and b.get("src", {}).get("pm")}
    peta_obj = {(b.get("jenis"), b["src"].get("nomor")): b for b in blok
                if b.get("jenis") in ("figure", "table")}
    baru_setelah = {}
    for b in blok:
        src = b.get("src", {})
        if src.get("baru") and src.get("insert_after"):
            baru_setelah.setdefault(src["insert_after"], []).append(b)
    emitted_baru_ids = set()

    def _emit_baru(anchor_id):
        for nb in baru_setelah.get(anchor_id, []):
            nb_id = nb.get("id")
            if nb_id in emitted_baru_ids:
                continue
            emitted_baru_ids.add(nb_id)
            if nb["jenis"] not in ("meta", "figure", "table"):
                if out and out[-1] != "":
                    out.append("")
                out.extend(_baris_blok_teks(nb, izinkan_pecah=False))
            if nb_id:
                _emit_baru(nb_id)

    dihapus_pm = set(dok.get("dihapus_pm", []))
    dihapus_obj = set(dok.get("dihapus_objek", []))
    baris = body_lama.split("\n")
    out = []
    i = 0
    while i < len(baris):
        b = baris[i]
        if _RE_HAPUS_DIRECTIVE.search(b):
            i += 1
            continue
        # buang lanjutan lama (sudah digabung ke dokumen)
        ml = _RE_LANJUTAN.match(b)
        if ml:
            # hapus #colbreak() tepat sebelumnya bila ada
            if out and out[-1].strip() == "#colbreak()":
                out.pop()
            i += 1
            continue
        tb = _urai_baris_teks(_RE_META.sub("", b))
        if tb and tb.get("pm") in dihapus_pm:
            i += 1
            continue
        if tb and tb.get("pm") in peta_pm:
            blk = peta_pm[tb["pm"]]
            j_next = i + 1
            while j_next < len(baris) and not baris[j_next].strip():
                j_next += 1
            if j_next < len(baris) and baris[j_next].strip() in ("#colbreak()", "#colbreak(weak: true)"):
                j_next += 1
                while j_next < len(baris) and not baris[j_next].strip():
                    j_next += 1
            ml_next = _RE_LANJUTAN.match(baris[j_next]) if j_next < len(baris) else None
            if ml_next and blk["id"] not in baru_setelah:
                st = blk.get("style") or {}
                untouched = (
                    not st.get("frozen")
                    and not st.get("unlocked_in_freeze")
                    and abs(angka(st.get("freeze_x"))) <= 0.05
                    and abs(angka(st.get("freeze_y"))) <= 0.05
                    and angka(st.get("freeze_w")) <= 1.0
                    and angka(st.get("freeze_slot_h")) <= 1.0
                    and abs(angka(st.get("tracking"))) <= 0.0005
                    and abs(angka(st.get("leading"))) <= 0.0005
                    and abs(angka(st.get("spacing_above"))) <= 0.0005
                    and abs(angka(st.get("spacing_below"))) <= 0.0005
                    and st.get("break_before", "none") == "none"
                    and not st.get("no_indent")
                    and not st.get("keep_with_next")
                )
                gabung_asal = (tb.get("content", "").rstrip() + " " + _buang_penanda_akhir(ml_next.group(2)).lstrip()).strip()
                if untouched and _RE_PE_PF.sub("", blk.get("content", "")).strip() == gabung_asal:
                    out.extend(baris[i:j_next + 1])
                    i = j_next + 1
                    continue
            pindah_blk = (blk.get("style") or {}).get("break_before", "none")
            if pindah_blk in ("column", "page"):
                target_breaks = ("#colbreak()", "#colbreak(weak: true)") if pindah_blk == "column" else ("#pagebreak()", "#pagebreak(weak: true)")
                k_end = len(out) - 1
                while k_end >= 0 and not out[k_end].strip():
                    k_end -= 1
                if k_end >= 0 and out[k_end].strip() in target_breaks:
                    del out[k_end:]
            out.extend(_baris_blok_teks(blk, multi_seg_ids=multi_seg_ids, izinkan_pecah=True))
            _emit_baru(blk["id"])
            i += 1
            continue
        # gambar
        mg = _RE_GAMBAR.match(b)
        if mg:
            nomor = None
            mnom = re.search(r"nomor:\s*(\d+)", b)
            if mnom:
                nomor = int(mnom.group(1))
            if nomor in dihapus_obj:
                i += 1
                continue
            blk = peta_obj.get(("figure", nomor))
            if blk:
                atas = angka(blk.get("style", {}).get("spacing_above"))
                if abs(atas) > 0.0005:
                    out.append("#v(%s) // ed:%s" % (fmt_pt(atas), blk["id"]))
                out.append(render_gambar(blk))
                _emit_baru(blk["id"])
                i += 1
                continue
        # tabel
        mt = _RE_MULAI_TABEL.match(b)
        if mt:
            sisa = "\n".join(baris[i:])
            kp = sisa.find("(")
            kt = cari_penutup(sisa, kp, "(", ")")
            kb = sisa.find("[", kt) if kt >= 0 else -1
            ktutup = cari_penutup(sisa, kb, "[", "]") if kb >= 0 else -1
            if ktutup >= 0:
                args = sisa[kp + 1:kt]
                mnom = re.search(r"nomor:\s*(\d+)", args)
                nomor = int(mnom.group(1)) if mnom else None
                if nomor in dihapus_obj:
                    i += sisa[:ktutup].count("\n") + 1
                    continue
                blk = peta_obj.get(("table", nomor))
                if blk:
                    atas = angka(blk.get("style", {}).get("spacing_above"))
                    if abs(atas) > 0.0005:
                        out.append("#v(%s) // ed:%s" % (fmt_pt(atas), blk["id"]))
                    body_tabel = blk.get("table_body", "")
                    out.append(render_tabel(blk, body_tabel, multi_seg_ids=multi_seg_ids) + _prefiks_gaya_tabel(blk)
                               + body_tabel + "]")
                    _emit_baru(blk["id"])
                    i += sisa[:ktutup].count("\n") + 1
                    continue
        out.append(b)
        i += 1
    # blok baru yang belum punya jangkar (mis. halaman kosong)
    for daftar in baru_setelah.values():
        for nb in daftar:
            nb_id = nb.get("id")
            if nb_id in emitted_baru_ids:
                continue
            emitted_baru_ids.add(nb_id)
            if nb["jenis"] not in ("meta", "figure", "table"):
                if out and out[-1] != "":
                    out.append("")
                out.extend(_baris_blok_teks(nb, izinkan_pecah=False))
    return "\n".join(out)


# ======================================================================
#  Riwayat
# ======================================================================
def _folder_riwayat(folder):
    d = os.path.join(folder, FOLDER_RIWAYAT)
    os.makedirs(d, exist_ok=True)
    return d


def _baca_indeks(folder):
    p = os.path.join(_folder_riwayat(folder), "index.json")
    return baca_json(p, {"pos": -1, "items": []}) or {"pos": -1, "items": []}


def _tulis_indeks(folder, idx):
    tulis_json(os.path.join(_folder_riwayat(folder), "index.json"), idx)


def _snapshot_path(folder, seq, akhiran):
    return os.path.join(_folder_riwayat(folder), "%06d.%s" % (seq, akhiran))


def _simpan_snapshot(folder, dok, body, meta, label, paksa=False):
    idx = _baca_indeks(folder)
    isi_sekarang = {"doc": dok, "body": body, "meta": meta}
    h = hash_teks(body + json.dumps(dok.get("blocks", []), sort_keys=True) + json.dumps(meta, sort_keys=True))
    if idx["items"] and not paksa:
        terakhir = idx["items"][idx["pos"]]
        if terakhir.get("hash") == h:
            return idx
    seq = max([x.get("seq", 0) for x in idx["items"]] or [0]) + 1
    tulis_json(_snapshot_path(folder, seq, "json"), dok)
    with open(_snapshot_path(folder, seq, "body.typ"), "w", encoding="utf-8") as f:
        f.write(body)
    tulis_json(_snapshot_path(folder, seq, "meta.json"), meta)
    item = {"seq": seq, "waktu": sekarang(), "label": label, "hash": h,
            "doc": "%06d.json" % seq, "body": "%06d.body.typ" % seq, "meta": "%06d.meta.json" % seq}
    idx["items"] = idx["items"][:idx["pos"] + 1] + [item]
    idx["pos"] = len(idx["items"]) - 1
    # batasi 40 langkah (minimal 20 sesuai kebutuhan)
    while len(idx["items"]) > 40:
        buang = idx["items"].pop(0)
        for k in ("doc", "body", "meta"):
            try:
                os.remove(_snapshot_path(folder, buang["seq"], {"doc": "json", "body": "body.typ",
                                                                 "meta": "meta.json"}[k]))
            except OSError:
                pass
        idx["pos"] -= 1
    _tulis_indeks(folder, idx)
    return idx


def _pastikan_snapshot(folder, dok, body, meta, label, paksa=False):
    idx = _baca_indeks(folder)
    if not idx["items"] or paksa:
        return _simpan_snapshot(folder, dok, body, meta, label, paksa=paksa)
    return idx


def _muat_snapshot(folder, item):
    dok = baca_json(os.path.join(_folder_riwayat(folder), item["doc"]))
    body = baca_teks(os.path.join(_folder_riwayat(folder), item["body"]))
    meta = baca_json(os.path.join(_folder_riwayat(folder), item["meta"]), {}) or {}
    return dok, body, meta


def riwayat_info(folder):
    idx = _baca_indeks(folder)
    return {"pos": idx["pos"], "jumlah": len(idx["items"]),
            "batal": max(0, idx["pos"]), "ulang": max(0, len(idx["items"]) - 1 - idx["pos"])}


# ======================================================================
#  Tulis keadaan + kompilasi
# ======================================================================
def _tulis_keadaan(folder, dok, body, meta):
    if meta is not None:
        tulis_json(os.path.join(folder, "meta.json"), meta)
    with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
        f.write(body)


# ======================================================================
#  Kompilasi SVG mandiri untuk web editor.
#  build.py produksi tidak lagi menyediakan kompilasi_svg(), jadi web UI
#  memanggil CLI Typst langsung dan membangun layout_map sendiri.
# ======================================================================
def _program_typst():
    return os.environ.get("TYPST_BIN") or os.path.join(BASE, "bin", "typst")


def _jalankan_typst(argv, batas=300):
    env = dict(os.environ)
    env.setdefault("TYPST_BIN", _program_typst())
    env.setdefault("LANG", "C.UTF-8")
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=BASE, env=env, timeout=batas)
    except subprocess.TimeoutExpired:
        return 124, "", "typst melebihi batas %d detik" % batas
    except OSError as e:
        return 127, "", "typst tidak dapat dijalankan: %s" % e
    return (r.returncode, r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))


def _kueri_posisi_typst(main_typ):
    """Metadata berlabel dari Typst -> {label: (nilai, halaman, x, y)}."""
    kueri = ('query(metadata).filter(it => it.has("label")).map(it => (str(it.label), it.value, '
             'it.location().page(), it.location().position().x.pt(), it.location().position().y.pt()))')
    rc, out, err = _jalankan_typst([
        _program_typst(), "eval", "--root", BASE, "--font-path",
        os.path.join(BASE, "fonts"), "--in", main_typ, kueri])
    if rc != 0:
        return None, err[-300:]
    try:
        data = json.loads(out)
        return {lbl: (nilai, hal, x, y) for lbl, nilai, hal, x, y in data}, ""
    except (ValueError, TypeError):
        return None, (err or out)[-300:]


def _kurangi_rintangan(y_a, y_b, rintangan):
    """Potong interval vertikal [y_a, y_b] dengan daftar rintangan [(ra, rb), ...]."""
    potongan = [(y_a, y_b)]
    for ra, rb in sorted(rintangan):
        baru = []
        for a, b in potongan:
            if rb <= a + 0.5 or ra >= b - 0.5:
                baru.append((a, b))
            else:
                if ra - a >= 9.0:
                    baru.append((a, ra))
                if b - rb >= 9.0:
                    baru.append((rb, b))
        potongan = baru
    return potongan


def _segmen_peta(mulai, akhir, penuh, lebar, atas_hal=None, bawah_hal=None, rintangan_kol=None):
    """Pecah (halaman,x,y) -> (halaman,x,y) menjadi kotak per kolom yang menghormati area abstrak hal 1 & objek."""
    if not mulai:
        return []
    atas_fn = (lambda h: atas_hal.get(h, MARGIN_ATAS)) if isinstance(atas_hal, dict) else (lambda _h: MARGIN_ATAS)
    bawah_fn = (lambda h: bawah_hal.get(h, BAWAH_KOLOM)) if isinstance(bawah_hal, dict) else (lambda _h: BAWAH_KOLOM)

    hal0, x0, y0 = mulai
    if akhir is None:
        return [(hal0, 0 if penuh else (1 if x0 < 290 else 2), y0, 11.0)]
    hal1, x1, y1 = akhir
    if penuh:
        if hal1 < hal0 or (hal1 == hal0 and y1 < y0):
            return [(hal0, 0, y0, 11.0)]
        if hal0 == hal1:
            return [(hal0, 0, y0, max(11.0, y1 - y0))]
        seg = [(hal0, 0, y0, max(11.0, bawah_fn(hal0) - y0))]
        for h in range(hal0 + 1, hal1):
            at = atas_fn(h)
            seg.append((h, 0, at, max(11.0, bawah_fn(h) - at)))
        at_akhir = atas_fn(hal1)
        seg.append((hal1, 0, at_akhir, max(11.0, y1 - at_akhir)))
        return seg

    k0 = 1 if x0 < 290 else 2
    k1 = 1 if x1 < 290 else 2
    if hal1 < hal0 or (hal1 == hal0 and k1 < k0):
        return [(hal0, k0, y0, 11.0)]

    mentah = []
    if hal0 == hal1 and k0 == k1:
        mentah.append((hal0, k0, y0, max(y0 + 11.0, y1)))
    else:
        mentah.append((hal0, k0, y0, max(y0 + 11.0, bawah_fn(hal0))))
        h, k = hal0, k0
        while (h, k) != (hal1, k1):
            if k == 1:
                k = 2
            else:
                k = 1
                h += 1
            at = atas_fn(h)
            if (h, k) == (hal1, k1):
                mentah.append((h, k, at, max(at + 11.0, y1)))
            else:
                mentah.append((h, k, at, max(at + 11.0, bawah_fn(h))))

    if not rintangan_kol:
        return [(h, k, ya, max(11.0, yb - ya)) for h, k, ya, yb in mentah]

    hasil = []
    for h, k, ya, yb in mentah:
        obs = list(rintangan_kol.get((h, 0), [])) + list(rintangan_kol.get((h, k), []))
        sub = _kurangi_rintangan(ya, yb, obs)
        if not sub:
            hasil.append((h, k, ya, max(11.0, yb - ya)))
        else:
            for sa, sb in sub:
                hasil.append((h, k, sa, max(10.0, sb - sa)))
    return hasil


def _simpan_peta_tata_letak(workdir, pos, halaman=None):
    """Tulis layout_map.json presisi: kolom 2 hlm 1 selalu mulai di bawah abstrak/sitasi."""
    workdir = os.path.abspath(workdir)
    dok = muat_dokumen(workdir, tulis=False)
    body_txt = baca_teks(os.path.join(workdir, "body.typ"))
    meta = baca_json(os.path.join(workdir, "meta.json"), {}) or {}
    awal = int((meta.get("terbitan") or {}).get("halaman_awal") or 1)
    blocks = dok.get("blocks", [])

    # Batas atas area 2-kolom di Halaman 1 wajib di bawah blok Header/Abstrak/Sitasi.
    pm_hal1 = [v[3] for k, v in pos.items() if k.startswith("pm-") and v[1] == 1]
    sitasi_akhir = pos.get("blk-akhir-m-sitasi") or pos.get("blk-akhir-m-abstrak")
    if pm_hal1:
        y_atas_hal1 = min(pm_hal1)
    elif sitasi_akhir and sitasi_akhir[1] == 1:
        y_atas_hal1 = sitasi_akhir[3] + 5.0
    else:
        y_atas_hal1 = 562.8

    kaki_map = {int(h): float(d) for h, d in re.findall(r"^#kaki-naik\((\d+),\s*(-?[\d.]+)pt\)", body_txt, flags=re.M)}
    kepala_map = {int(h): float(d) for h, d in re.findall(r"^#kepala-turun\((\d+),\s*(-?[\d.]+)pt\)", body_txt, flags=re.M)}
    jml_hal = int(halaman or max([v[1] for v in pos.values()] or [1]))
    atas_hal = {h: (y_atas_hal1 if h == 1 else MARGIN_ATAS + kepala_map.get(h, 0.0)) for h in range(1, jml_hal + 2)}
    bawah_hal = {h: BAWAH_KOLOM - kaki_map.get(h, 0.0) for h in range(1, jml_hal + 2)}

    # Kumpulkan kotak objek (figure/table) terlebih dahulu agar menjadi pembatas segmen teks.
    peta = []
    rintangan_kol = {}
    for b in blocks:
        if b.get("jenis") not in ("figure", "table"):
            continue
        src = b.get("src", {})
        bid = b.get("id")
        jenis = b.get("jenis")
        label = b.get("judul") or "Blok"
        teks = (b.get("text") or "").strip().replace("\n", " ")
        nomor = src.get("nomor")
        v = pos.get("objek-info-{}".format(nomor))
        if not v:
            continue
        nilai, hal, x, y = v
        tinggi = 0.0
        if isinstance(nilai, (list, tuple)) and len(nilai) > 1:
            try:
                tinggi = float(nilai[1])
            except (TypeError, ValueError):
                tinggi = 0.0
        st_obj = b.get("style", {}) or {}
        fx = angka(st_obj.get("freeze_x"), 0.0)
        fy = angka(st_obj.get("freeze_y"), 0.0)
        sh_obj = angka(st_obj.get("freeze_slot_h"), 0.0)
        is_frozen = bool(st_obj.get("frozen")) or bool(st_obj.get("unlocked_in_freeze")) or abs(fx) > 0.05 or abs(fy) > 0.05 or sh_obj > 1.0

        penuh = st_obj.get("span") == "page" or bool(b.get("penuh"))
        if is_frozen:
            x_box = x
        else:
            x_box = X_KIRI if penuh else (X_KIRI if x < 290 else X_KANAN)
        lebar = LEBAR_TEKS if penuh else LEBAR_KOLOM
        a = (pos.get("objek-akhir-beku-{}".format(nomor)) if is_frozen else None) or pos.get("objek-akhir-{}".format(nomor))
        akhir = (a[1], a[2], a[3]) if a else None
        if akhir is None:
            seg = _segmen_peta((hal, x_box, y), None, penuh, lebar, atas_hal, bawah_hal)
            if seg:
                seg[0] = (seg[0][0], seg[0][1], seg[0][2], max(11.0, tinggi or 60.0))
        else:
            seg = _segmen_peta((hal, x_box, y), akhir, penuh, lebar, atas_hal, bawah_hal)
        for i, (h, kol, yy, hh) in enumerate(seg):
            base_yy = (yy - fy) if is_frozen else yy
            rintangan_kol.setdefault((h, 0 if penuh else kol), []).append((base_yy - 2.0, base_yy + hh + 2.0))
            seg_x = (X_KIRI if penuh else (X_KIRI if kol == 1 else X_KANAN)) + fx if (is_frozen and len(seg) > 1 and i > 0) else x_box
            entry = {"id": bid, "type": jenis, "label": label,
                     "nomor": nomor, "page": h, "printed_page": awal + h - 1,
                     "x": round(seg_x, 3), "y": round(yy, 3), "width": round(lebar, 3),
                     "height": round(hh, 3), "column": kol, "segment": i,
                     "span": "page" if penuh else "column",
                     "text": teks[:70]}
            if is_frozen:
                entry["frozen"] = True
                entry["freeze_x"] = round(fx, 3)
                entry["freeze_y"] = round(fy, 3)
            peta.append(entry)

    # Indeks penanda awal/akhir fragmen #pf/#pe dari Typst
    frag_awal_by_idx = {}
    frag_akhir_by_idx = {}
    frag_akhir_pm = {}
    for k_pos, v_pos in pos.items():
        ma = re.fullmatch(r"frag-awal-(\d+)-(\d+)", k_pos)
        if ma:
            frag_awal_by_idx.setdefault(int(ma.group(1)), {})[int(ma.group(2))] = v_pos
        mf = re.fullmatch(r"frag-akhir-(\d+)-(\d+)", k_pos)
        if mf:
            pm_i, seg_i = int(mf.group(1)), int(mf.group(2))
            frag_akhir_pm.setdefault(pm_i, []).append(v_pos)
            frag_akhir_by_idx.setdefault(pm_i, {})[seg_i] = v_pos

    teks_berurut = [b for b in blocks
                    if b.get("jenis") not in ("meta", "figure", "table")
                    and pos.get("pm-{}".format(b.get("src", {}).get("pm")))]
    berikut_teks = {teks_berurut[i]["id"]: (teks_berurut[i + 1] if i + 1 < len(teks_berurut) else None)
                    for i in range(len(teks_berurut))}

    meta_ada = set()
    for b in blocks:
        jenis = b.get("jenis")
        if jenis in ("figure", "table"):
            continue
        src = b.get("src", {})
        bid = b.get("id")
        label = b.get("judul") or "Blok"
        teks = (b.get("text") or "").strip().replace("\n", " ")

        if jenis == "meta":
            kunci = b.get("meta_key")
            v = pos.get("meta-" + kunci.replace("_", "-"))
            if not v:
                continue
            meta_ada.add(kunci)
            _, hal, x, y = v
            a = pos.get("blk-akhir-m-" + kunci.replace("_", "-"))
            akhir = (a[1], a[2], a[3]) if a else None
            if kunci in ("judul", "penulis", "afiliasi", "sitasi"):
                x0, lebar = X_KIRI, LEBAR_TEKS
            elif kunci == "abstrak":
                x0, lebar = X_KIRI + 150.0, 292.2
            else:
                x0, lebar = X_KIRI, 130.0
            seg = _segmen_peta((hal, x0, y), akhir, True, lebar, atas_hal, bawah_hal)
            if not akhir and seg:
                seg[0] = (seg[0][0], seg[0][1], seg[0][2], 18.0)
            for i, (h, kol, yy, hh) in enumerate(seg):
                peta.append({"id": bid, "type": "meta", "label": label,
                             "page": h, "printed_page": awal + h - 1,
                             "x": round(x0, 3), "y": round(yy, 3), "width": round(lebar, 3),
                             "height": round(max(10.0, hh), 3), "column": kol, "segment": i,
                             "span": "page", "text": teks[:70]})
            continue

        pm = src.get("pm")
        v = pos.get("pm-{}".format(pm))
        if not v:
            continue
        _, hal, x, y = v

        st_teks = b.get("style", {}) or {}
        fx = angka(st_teks.get("freeze_x"), 0.0)
        fy = angka(st_teks.get("freeze_y"), 0.0)
        fw = angka(st_teks.get("freeze_w"), 0.0)
        sh_teks = angka(st_teks.get("freeze_slot_h"), 0.0)
        is_frozen = bool(st_teks.get("frozen")) or bool(st_teks.get("unlocked_in_freeze")) or abs(fx) > 0.05 or abs(fy) > 0.05 or fw > 1.0 or sh_teks > 1.0
        lebar_teks_box = round(fw, 3) if (is_frozen and fw > 1.0) else round(LEBAR_KOLOM, 3)

        akhir = None
        ve_teks = None
        if pm in frag_akhir_pm:
            ve_teks = max(frag_akhir_pm[pm], key=lambda item: (item[1], 0 if item[2] < 290 else 1, item[3]))
            akhir = (ve_teks[1], ve_teks[2], ve_teks[3] + 2.2)
        if akhir is None:
            a = pos.get("blk-akhir-{}".format(bid))
            if a:
                ve_teks = a
                akhir = (a[1], a[2], a[3] + 2.2)

        if is_frozen:
            f_awal_map = frag_awal_by_idx.get(pm, {})
            f_akhir_map = frag_akhir_by_idx.get(pm, {})
            if len(f_awal_map) >= 2:
                for seg_idx in sorted(f_awal_map):
                    a_pos = f_awal_map[seg_idx]
                    e_pos = f_akhir_map.get(seg_idx)
                    _, h_s, x_s, y_s = a_pos
                    k_s = 1 if (x_s - fx) < 296 else 2
                    x_s_box = round((X_KIRI if k_s == 1 else X_KANAN) + fx, 3)
                    same_spot = (e_pos is not None and e_pos[1] == h_s
                                 and ((1 if (e_pos[2] - fx) < 296 else 2) == k_s))
                    hh = max(11.0, (e_pos[3] + 2.2) - y_s) if same_spot else 11.0
                    peta.append({"id": bid, "type": jenis, "label": label,
                                 "pm": pm, "page": h_s, "printed_page": awal + h_s - 1,
                                 "x": x_s_box, "y": round(y_s, 3), "width": lebar_teks_box,
                                 "height": round(hh, 3), "column": k_s, "segment": seg_idx,
                                 "span": "column", "text": teks[:70],
                                 "frozen": True, "freeze_x": round(fx, 3), "freeze_y": round(fy, 3)})
                continue
            k0 = 1 if (x - fx) < 296 else 2
            x_box = round((X_KIRI if k0 == 1 else X_KANAN) + fx, 3)
            if ve_teks is not None and ve_teks[1] == hal and (
                abs(fx) > 0.05 or abs(fy) > 0.05 or fw > 1.0 or sh_teks > 1.0 or ((ve_teks[2] - fx) < 296) == ((x - fx) < 296)
            ):
                hh = max(11.0, (ve_teks[3] + 2.2) - y)
                peta.append({"id": bid, "type": jenis, "label": label,
                             "pm": pm, "page": hal, "printed_page": awal + hal - 1,
                             "x": x_box, "y": round(y, 3), "width": lebar_teks_box,
                             "height": round(hh, 3), "column": k0, "segment": 0,
                             "span": "column", "text": teks[:70],
                             "frozen": True, "freeze_x": round(fx, 3), "freeze_y": round(fy, 3)})
                continue
            elif ve_teks is None and (abs(fx) > 0.05 or abs(fy) > 0.05 or fw > 1.0 or sh_teks > 1.0):
                peta.append({"id": bid, "type": jenis, "label": label,
                             "pm": pm, "page": hal, "printed_page": awal + hal - 1,
                             "x": x_box, "y": round(y, 3), "width": lebar_teks_box,
                             "height": 11.0, "column": k0, "segment": 0,
                             "span": "column", "text": teks[:70],
                             "frozen": True, "freeze_x": round(fx, 3), "freeze_y": round(fy, 3)})
                continue

        if akhir is None:
            nx = berikut_teks.get(bid)
            if nx:
                v2 = pos.get("pm-{}".format(nx.get("src", {}).get("pm")))
                if v2:
                    akhir = (v2[1], v2[2], v2[3])
        seg = _segmen_peta((hal, x, y), akhir, False, LEBAR_KOLOM, atas_hal, bawah_hal, rintangan_kol)
        for i, (h, kol, yy, hh) in enumerate(seg):
            entry = {"id": bid, "type": jenis, "label": label,
                     "pm": pm, "page": h, "printed_page": awal + h - 1,
                     "x": round((X_KIRI if kol == 1 else X_KANAN) + (fx if is_frozen else 0.0), 3),
                     "y": round(yy, 3), "width": lebar_teks_box,
                     "height": round(hh, 3), "column": kol, "segment": i,
                     "span": "column", "text": teks[:70]}
            if is_frozen:
                entry["frozen"] = True
                entry["freeze_x"] = round(fx, 3)
                entry["freeze_y"] = round(fy, 3)
            peta.append(entry)

    # Fallback apabila ada field metadata kosong (mis. email kosong) yang tidak memancarkan label
    if halaman:
        blok_meta = {b.get("meta_key"): b for b in blocks if b.get("jenis") == "meta"}
        judul = (blok_meta.get("judul", {}).get("text") or "")
        baris_judul = max(1, min(4, (len(judul) + 62) // 63))
        geser = (baris_judul - 2) * 14.0
        abstrak = (blok_meta.get("abstrak", {}).get("text") or "")
        baris_abstrak = max(1, (len(abstrak) + 69) // 70)
        tinggi_abstrak = 10.0 + baris_abstrak * 10.0
        n_kw = max(1, len(meta.get("kata_kunci") or []))
        y_email = 254.9 + n_kw * 12.0 + 22.7 + geser
        y_sitasi = 182.1 + max(tinggi_abstrak, 173.5) + 10.6 + geser
        posisi_meta = {
            "judul": (X_KIRI, 102.0, LEBAR_TEKS, max(14.0, baris_judul * 13.5)),
            "penulis": (X_KIRI, 142.6 + geser, LEBAR_TEKS, 11.0),
            "afiliasi": (X_KIRI, 153.6 + geser, LEBAR_TEKS, 13.8),
            "abstrak": (X_KIRI + 150.0, 182.1 + geser, 292.2, tinggi_abstrak),
            "kata_kunci": (X_KIRI, 254.9 + geser, 130.0, max(20.0, n_kw * 12.0 + 9.0)),
            "riwayat_diterima": (X_KIRI, 206.8 + geser, 130.0, 9.0),
            "riwayat_disetujui": (X_KIRI, 218.8 + geser, 130.0, 9.0),
            "riwayat_terbit": (X_KIRI, 230.8 + geser, 130.0, 9.0),
            "email": (X_KIRI, y_email, 130.0, 18.0),
            "sitasi": (X_KIRI, y_sitasi, LEBAR_TEKS, 46.0),
        }
        for kunci in ("judul", "penulis", "afiliasi", "abstrak", "kata_kunci",
                      "riwayat_diterima", "riwayat_disetujui", "riwayat_terbit",
                      "email", "sitasi"):
            b = blok_meta.get(kunci)
            if not b or kunci in meta_ada:
                continue
            x0, y0, lebar, tinggi = posisi_meta[kunci]
            peta.append({"id": b.get("id") or ID_META[kunci], "type": "meta",
                         "label": LABEL_META.get(kunci, "Metadata"),
                         "page": 1, "printed_page": awal,
                         "x": round(x0, 3), "y": round(y0, 3),
                         "width": round(lebar, 3), "height": round(tinggi, 3),
                         "column": 0, "segment": 0, "span": "page",
                         "text": (b.get("text") or "")[:70]})

    hasil = {"versi": 2, "artikel": os.path.basename(workdir),
             "dibuat": time.strftime("%Y-%m-%d %H:%M:%S"),
             "lebar": 595.275590551, "tinggi": 841.88976378,
             "halaman": int(halaman) if halaman else max([x["page"] for x in peta] or [0]),
             "halaman_awal": awal, "y_atas_hal1": round(y_atas_hal1, 3), "blocks": peta}
    with open(os.path.join(workdir, "layout_map.json"), "w", encoding="utf-8") as f:
        json.dump(hasil, f, ensure_ascii=False, indent=2)
    return {"ok": True, "peta": hasil}


def kompilasi_svg(folder, buat_pdf=True):
    """Kompilasi main.typ -> hasil.pdf (untuk PDF.js) + _svg/hal-{p}.svg + layout_map.json."""
    folder = os.path.abspath(folder)
    main_typ = os.path.join(folder, "main.typ")
    if not os.path.isfile(main_typ) or not os.path.isfile(os.path.join(folder, "body.typ")):
        return {"ok": False, "galat": "main.typ/body.typ tidak ada di folder artikel"}
    try:
        muat_dokumen(folder)
    except Exception:  # noqa: BLE001
        pass
    svg_dir = os.path.join(folder, FOLDER_SVG)
    os.makedirs(svg_dir, exist_ok=True)
    for f in glob.glob(os.path.join(svg_dir, "hal-*.svg")):
        try:
            os.remove(f)
        except OSError:
            pass
    pola = os.path.join(svg_dir, "hal-{p}.svg")
    pdf_out = os.path.join(folder, "hasil.pdf")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_svg = ex.submit(_jalankan_typst, [
            _program_typst(), "compile", "--root", BASE, "--font-path",
            os.path.join(BASE, "fonts"), "--format", "svg", main_typ, pola])
        f_pos = ex.submit(_kueri_posisi_typst, main_typ)
        f_pdf = ex.submit(_jalankan_typst, [
            _program_typst(), "compile", "--root", BASE, "--font-path",
            os.path.join(BASE, "fonts"), main_typ, pdf_out]) if buat_pdf else None
        rc, out, err = f_svg.result()
        pos, galat = f_pos.result()
        if f_pdf is not None:
            rc_p, _, err_p = f_pdf.result()
            if rc == 0 and rc_p != 0:
                rc, err = rc_p, err_p
    if rc != 0:
        return {"ok": False, "galat": "\n".join(
            l for l in err.splitlines() if not l.startswith("warning"))[-3000:]}
    halaman = sorted(glob.glob(os.path.join(svg_dir, "hal-*.svg")),
                     key=lambda p: int(re.search(r"hal-(\d+)\.svg$", p).group(1)))
    if not halaman:
        return {"ok": False, "galat": "Typst tidak menghasilkan berkas SVG"}
    if pos is None:
        return {"ok": False, "galat": "Kueri posisi blok gagal: " + galat}
    peta = _simpan_peta_tata_letak(folder, pos, halaman=len(halaman))
    if not peta.get("ok"):
        return peta
    lebar, tinggi = 595.275590551, 841.88976378
    try:
        with open(halaman[0], encoding="utf-8") as f:
            awal = f.read(600)
        m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', awal)
        if m:
            lebar, tinggi = float(m.group(1)), float(m.group(2))
    except OSError:
        pass
    info = {"halaman": len(halaman), "lebar": lebar, "tinggi": tinggi,
            "berkas": [os.path.basename(x) for x in halaman], "waktu": time.time()}
    with open(os.path.join(svg_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    return {"ok": True, "halaman": len(halaman), "lebar": lebar, "tinggi": tinggi,
            "folder": svg_dir, "layout_map": peta}


def hash_sumber_qa(folder):
    """Cache QA harus mengikuti metadata dan aturan produksi, bukan hanya body."""
    paths = [os.path.join(folder, name) for name in ("body.typ", "meta.json", "main.typ")]
    paths += [os.path.join(BASE, name) for name in
              ("template_jurnal.typ", "build.py", "qa_cepat.py")]
    main = baca_teks(os.path.join(folder, "main.typ"))
    cfg = re.search(r'json\("([^"]*config\.json)"\)', main)
    if cfg:
        paths.append(os.path.join(BASE, cfg.group(1).lstrip("/")))
    return hash_teks(json.dumps([baca_teks(path) for path in paths], ensure_ascii=False))


def jalankan_qa(folder):
    """Jalankan qa_cepat.py tanpa gambar PNG; kembalikan dict."""
    folder = os.path.abspath(folder)
    py = os.environ.get("PYTHON_BIN") or sys.executable
    skrip = os.path.join(BASE, "qa_cepat.py")
    hash_awal = hash_teks(baca_teks(os.path.join(folder, "body.typ")))
    sumber_awal = hash_sumber_qa(folder)
    cmd = [py, skrip, folder, "--ppi", "80", "--tanpa-gambar"]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=BASE,
                           timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "galat": "QA melebihi batas 180 detik"}
    out = r.stdout.decode("utf-8", "replace")
    try:
        data = json.loads(out)
    except ValueError:
        data = {"ok": False, "galat": (r.stderr.decode("utf-8", "replace") or out)[-3000:]}
    if not data.get("ok"):
        return data
    qa = {"status": "DITAHAN" if data.get("masalah") else "LULUS",
          "masalah": data.get("masalah", []), "catatan": data.get("catatan", []),
          "kolom": data.get("kolom", []), "kaki_naik": data.get("kaki_naik", {}),
          "halaman": data.get("halaman"), "halaman_awal": data.get("halaman_awal"),
          "lebar": data.get("lebar"), "tinggi": data.get("tinggi"),
          "peringatan_typst": data.get("peringatan_typst", [])}
    # Tolak hasil versi lama jika body, metadata, atau template berubah selama QA.
    if hash_sumber_qa(folder) != sumber_awal:
        return dict(qa, ok=True, basi=True)
    tulis_json(os.path.join(folder, FOLDER_QA), {"versi": 3,
                                                  "body_hash": hash_awal,
                                                  "sumber_hash": sumber_awal,
                                                  "waktu": sekarang(), "qa": qa})
    # status untuk daftar artikel (format lama)
    nama = os.path.basename(folder.rstrip("/"))
    tulis_json(os.path.join(folder, nama + ".qa.json"),
               {"status": qa["status"], "masalah": qa["masalah"], "peringatan": qa["catatan"]})
    return qa


def kompilasi_penuh(folder, qa=True):
    """Kompilasi SVG + layout_map, lalu QA cepat."""
    svg = kompilasi_svg(folder)
    if not svg.get("ok"):
        return {"ok": False, "galat": svg.get("galat", "Kompilasi SVG gagal")}
    q = jalankan_qa(folder) if qa else {}
    if qa and not q.get("ok", True):
        return {"ok": False, "galat": q.get("galat", "QA gagal"), "svg": svg}
    return {"ok": True, "svg": svg, "qa": q}


def _qa_terbaru(folder):
    cache = baca_json(os.path.join(folder, FOLDER_QA), {}) or {}
    if (cache.get("versi") == 3 and cache.get("sumber_hash") == hash_sumber_qa(folder)
            and cache.get("qa")):
        return cache["qa"]
    return None


def aksi_qa(folder, data=None):
    """Periksa aturan secara terpisah dari get_workspace/update_block.

    Tanpa ``paksa`` cache yang masih cocok dikembalikan seketika. Endpoint ini sengaja
    tidak memakai lock artikel supaya QA berat tidak menahan penyuntingan teks.
    """
    folder = os.path.abspath(folder)
    paksa = bool((data or {}).get("paksa"))
    qa = None if paksa else _qa_terbaru(folder)
    if qa is None:
        qa = jalankan_qa(folder)
    body_hash = hash_teks(baca_teks(os.path.join(folder, "body.typ")))
    if qa.get("basi"):
        return {"ok": True, "basi": True, "body_hash": body_hash}
    if not qa.get("ok", True):
        return {"ok": False, "galat": qa.get("galat", "QA gagal"), "body_hash": body_hash}
    peta = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}
    versi = int(max(os.path.getmtime(os.path.join(folder, "body.typ")),
                    os.path.getmtime(os.path.join(folder, "layout_map.json"))
                    if os.path.isfile(os.path.join(folder, "layout_map.json")) else 0) * 1000)
    return {"ok": True, "qa": qa, "status": qa.get("status", ""), "body_hash": body_hash,
            "versi": versi, "halaman": int(qa.get("halaman") or peta.get("halaman") or 0),
            "halaman_awal": qa.get("halaman_awal", 1)}


# ======================================================================
#  Workspace
# ======================================================================
def _blok_untuk_ui(dok):
    hasil = []
    for b in dok.get("blocks", []):
        x = {"id": b["id"], "jenis": b["jenis"], "tipe": b.get("tipe"), "judul": b.get("judul"),
             "level": b.get("level"), "nomor": b.get("nomor"),
             "text": b.get("text", ""), "style": b.get("style", {})}
        if b.get("meta_key"):
            x["meta_key"] = b["meta_key"]
        if b.get("jenis") == "table":
            x["cells"] = b.get("cells", [])
        hasil.append(x)
    return hasil


def _info_doi(folder, meta, halaman):
    """DOI yang ditampilkan template: prefix jurnal + volume/nomor/halaman."""
    prefix = ""
    main = baca_teks(os.path.join(folder, "main.typ"))
    m = re.search(r'json\("([^"]*config\.json)"\)', main)
    if m:
        jalur = m.group(1).lstrip("/")
        cfg = baca_json(os.path.join(BASE, jalur), {}) or {}
        if not cfg and os.path.isabs(m.group(1)):
            cfg = baca_json(m.group(1), {}) or {}
        prefix = str(cfg.get("doi_prefix") or "")
    t = meta.get("terbitan") if isinstance(meta.get("terbitan"), dict) else {}
    try:
        awal = int(t.get("halaman_awal") or 1)
    except (TypeError, ValueError):
        awal = 1
    try:
        jumlah = max(1, int(halaman or 1))
    except (TypeError, ValueError):
        jumlah = 1
    akhir = awal + jumlah - 1
    vol = t.get("volume", "")
    nom = t.get("nomor", "")
    doi = ""
    if prefix:
        doi = "https://doi.org/%s/v%si%s.pp%s-%s" % (prefix, vol, nom, awal, akhir)
    return {"doi": doi, "doi_prefix": prefix, "halaman_akhir": akhir}


def _baca_batas_hal_body(body, direktif):
    hasil = {}
    pola = r"^#%s\((\d+),\s*(-?[\d.]+)pt\)" % re.escape(direktif)
    for m in re.finditer(pola, body or "", flags=re.M):
        hal = int(m.group(1))
        pt = float(m.group(2))
        baris = max(-2, min(2, int(round(pt / 11.0))))
        if baris != 0:
            hasil[str(hal)] = baris
    return hasil


def _baca_kaki_body(body):
    return _baca_batas_hal_body(body, "kaki-naik")


def _baca_kepala_body(body):
    return _baca_batas_hal_body(body, "kepala-turun")


_VALID_GRID_SHIFT = (-22, -11, 0, 11, 22)


def _normal_grid_shift(val):
    if val is None or val == "":
        return None
    try:
        v = int(round(float(val) / 11.0)) * 11
    except (TypeError, ValueError):
        return 0
    return max(-22, min(22, v))


def _baca_header_footer_main(folder):
    main = baca_teks(os.path.join(folder, "main.typ"))
    m = re.search(r"^#show:\s*artikel\.with\(cfg,\s*meta(?P<args>[^)\n]*)\)", main, flags=re.M)
    if not m:
        return 0, 0
    args = m.group("args") or ""
    mh = re.search(r"header-grid:\s*(-?\d+)pt", args)
    mf = re.search(r"footer-grid:\s*(-?\d+)pt", args)
    hs = _normal_grid_shift(mh.group(1)) if mh else 0
    fs = _normal_grid_shift(mf.group(1)) if mf else 0
    return (hs if hs in _VALID_GRID_SHIFT else 0), (fs if fs in _VALID_GRID_SHIFT else 0)


def _terapkan_header_footer_main(main_text, header_shift=None, footer_shift=None):
    pat = re.compile(r"^(#show:\s*artikel\.with\(cfg,\s*meta)([^)\n]*)\)$", flags=re.M)
    m = pat.search(main_text or "")
    if not m:
        return main_text, False
    args = m.group(2) or ""
    mh = re.search(r"header-grid:\s*(-?\d+)pt", args)
    mf = re.search(r"footer-grid:\s*(-?\d+)pt", args)
    cur_h = _normal_grid_shift(mh.group(1)) if mh else 0
    cur_f = _normal_grid_shift(mf.group(1)) if mf else 0
    new_h = _normal_grid_shift(header_shift) if header_shift is not None else cur_h
    new_f = _normal_grid_shift(footer_shift) if footer_shift is not None else cur_f
    if new_f != 0:
        rep = "%s, header-grid: %dpt, footer-grid: %dpt)" % (m.group(1), new_h, new_f)
    else:
        rep = "%s, header-grid: %dpt)" % (m.group(1), new_h)
    baru = main_text[:m.start()] + rep + main_text[m.end():]
    return baru, (baru != main_text)


def ruang_kerja(folder, paksa_kompilasi=False, sertakan_qa=False, cache_saja=False):
    """Bangun payload editor.

    ``cache_saja=True`` adalah jalur get_workspace: bila SVG halaman 1 + layout_map
    sudah ada, jangan pernah kompilasi ulang dan jangan pernah menjalankan QA/PDF.
    QA hanya berjalan di aksi ``qa`` atau bila ``sertakan_qa=True`` diminta eksplisit.
    """
    folder = os.path.abspath(folder)
    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    # pastikan snapshot awal
    _pastikan_snapshot(folder, dok, body, meta, "Keadaan awal editor")
    svg_dir = os.path.join(folder, FOLDER_SVG)
    map_path = os.path.join(folder, "layout_map.json")
    if cache_saja:
        perlu = paksa_kompilasi or not os.path.isfile(os.path.join(svg_dir, "hal-1.svg")) \
            or not os.path.isfile(map_path)
    else:
        perlu = paksa_kompilasi or not os.path.isdir(svg_dir) or not os.path.isfile(map_path)
        if not perlu:
            mtime_body = max(os.path.getmtime(os.path.join(folder, "body.typ")),
                             os.path.getmtime(os.path.join(folder, "meta.json")) if os.path.isfile(os.path.join(folder, "meta.json")) else 0)
            mtime_map = os.path.getmtime(map_path)
            if mtime_map < mtime_body:
                perlu = True
    if not perlu:
        # Dokumen lama yang baru mendapat blok metadata tambahan harus dipetakan
        # ulang agar overlay Article History/email/sitasi langsung bisa diklik.
        peta_awal = baca_json(map_path, {}) or {}
        if peta_awal.get("versi") != 2:
            perlu = True
        else:
            id_peta = {s.get("id") for s in peta_awal.get("blocks", [])}
            if any(b.get("jenis") == "meta" and b.get("id") not in id_peta
                   for b in dok.get("blocks", [])):
                perlu = True
    if perlu:
        k = kompilasi_svg(folder)
        if not k.get("ok"):
            return {"ok": False, "galat": k.get("galat", "Kompilasi gagal")}
    qa = _qa_terbaru(folder)
    if qa is None and sertakan_qa:
        qa = jalankan_qa(folder)
        if qa.get("basi"):
            qa = None
        elif not qa.get("ok", True):
            return {"ok": False, "galat": qa.get("galat", "QA gagal")}
    peta = baca_json(map_path, {}) or {}
    halaman = max(int(peta.get("halaman") or 0), int(qa.get("halaman") or 0) if qa else 0)
    versi = int(max(os.path.getmtime(os.path.join(folder, "body.typ")),
                    os.path.getmtime(map_path) if os.path.isfile(map_path) else 0) * 1000)
    pages = [{"p": p, "url": "api.php?aksi=svg&artikel=%s&p=%d&v=%d" %
              (os.path.basename(folder), p, versi), "lebar": peta.get("lebar", LEBAR_HALAMAN),
              "tinggi": peta.get("tinggi", TINGGI_HALAMAN)} for p in range(1, int(halaman) + 1)]
    h_shift, f_shift = _baca_header_footer_main(folder)
    return {"ok": True, "artikel": os.path.basename(folder), "versi": versi,
            "body_hash": hash_teks(body),
            "halaman": int(halaman),
            "halaman_awal": (qa.get("halaman_awal") if qa else None)
            or meta.get("terbitan", {}).get("halaman_awal", 1),
            "lebar": peta.get("lebar", LEBAR_HALAMAN), "tinggi": peta.get("tinggi", TINGGI_HALAMAN),
            "blocks": _blok_untuk_ui(dok), "pages": pages, "layout_map": peta,
            "kaki_naik": _baca_kaki_body(body),
            "header_naik": _baca_kepala_body(body),
            "header_shift": h_shift, "footer_shift": f_shift,
            "qa": qa, "riwayat": riwayat_info(folder),
            "terbitan": meta.get("terbitan") or {},
            **_info_doi(folder, meta, halaman),
            "status": qa.get("status", "") if qa else ""}


def _halaman_berubah(peta_lama, peta_baru, fokus=None):
    """Halaman yang perlu dimuat ulang setelah satu perubahan blok."""
    def petakan(peta):
        return {(s.get("id"), s.get("segment")): s
                for s in (peta or {}).get("blocks", [])}
    lama, baru = petakan(peta_lama), petakan(peta_baru)
    berubah = set()
    for kunci in set(lama) | set(baru):
        a, b = lama.get(kunci), baru.get(kunci)
        if a != b:
            if a:
                berubah.add(int(a.get("page") or 1))
            if b:
                berubah.add(int(b.get("page") or 1))
    n_lama = int((peta_lama or {}).get("halaman") or 0)
    n_baru = int((peta_baru or {}).get("halaman") or 0)
    if n_lama != n_baru:
        berubah.update(range(1, max(n_lama, n_baru) + 1))
    if fokus:
        for s in (peta_lama or {}).get("blocks", []) + (peta_baru or {}).get("blocks", []):
            if s.get("id") == fokus:
                berubah.add(int(s.get("page") or 1))
    return sorted(berubah)


def _terapkan_dan_kompilasi(folder, dok, body, meta, main_text=None):
    """Tulis body/meta/dokumen, kompilasi; bila gagal pulihkan ketiganya."""
    path_dok = os.path.join(folder, NAMA_DOKUMEN)
    body_lama = baca_teks(os.path.join(folder, "body.typ"))
    meta_lama = baca_teks(os.path.join(folder, "meta.json"))
    dok_lama = baca_teks(path_dok)
    main_path = os.path.join(folder, "main.typ")
    main_lama = baca_teks(main_path)
    _tulis_keadaan(folder, dok, body, meta)
    if main_text is not None:
        with open(main_path, "w", encoding="utf-8") as f:
            f.write(main_text)
    # simpan dokumen SEBELUM kompilasi agar layout_map memakai style terbaru
    simpan_dokumen(folder, dok)
    # Jalur edit harus cepat: SVG + layout_map saja. PDF dan PyMuPDF dijalankan
    # terpisah lewat aksi qa setelah pengguna berhenti mengetik.
    k = kompilasi_penuh(folder, qa=False)
    if not k.get("ok"):
        with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
            f.write(body_lama)
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
            f.write(meta_lama)
        with open(path_dok, "w", encoding="utf-8") as f:
            f.write(dok_lama)
        if main_text is not None:
            with open(main_path, "w", encoding="utf-8") as f:
                f.write(main_lama)
        # SVG/layout_map yang sempat dibuat dari isi yang ditolak harus dibuang
        try:
            os.remove(os.path.join(folder, "layout_map.json"))
        except OSError:
            pass
        shutil.rmtree(os.path.join(folder, FOLDER_SVG), ignore_errors=True)
        return k
    return {"ok": True, "qa": k.get("qa"), "svg": k.get("svg")}


# ======================================================================
#  Aksi
# ======================================================================
def aksi_update(folder, data):
    folder = os.path.abspath(folder)
    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    peta_lama = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}
    bid = str(data.get("block_id", ""))
    blok = next((b for b in dok["blocks"] if b.get("id") == bid), None)
    if not blok:
        return {"ok": False, "galat": "Blok %s tidak ditemukan" % bid}
    src0 = blok.get("src", {})
    if blok.get("jenis") not in ("meta", "figure", "table") and src0.get("pm"):
        if "#pm(%s);" % src0["pm"] not in body:
            return {"ok": False, "galat": "Penanda blok %s tidak ada lagi di body.typ" % bid}
    elif blok.get("jenis") in ("figure", "table") and src0.get("nomor"):
        jenis_obj = "gambar" if blok.get("jenis") == "figure" else "tabel"
        if _baris_objek(body, jenis_obj, src0["nomor"]) is None:
            return {"ok": False, "galat": "Objek %s tidak ada lagi di body.typ" % bid}
    sebelum = json.dumps(blok, sort_keys=True, ensure_ascii=False)
    if "text" in data and data["text"] is not None:
        teks = str(data["text"])
        if blok.get("jenis") == "meta":
            blok["text"] = teks
            blok["content"] = teks
        elif blok.get("jenis") in ("figure", "table"):
            st = normal_gaya(blok["jenis"], blok.get("style"))
            st["caption"] = teks.replace("\n", " ").strip()
            blok["style"] = st
            blok["text"] = st["caption"]
        else:
            blok["content"] = typst_dari_teks_ui(teks)
            blok["text"] = teks
    if blok.get("jenis") != "meta" and "style" in data and isinstance(data["style"], dict):
        gabung = dict(blok.get("style", {}))
        gabung.update(data["style"])
        blok["style"] = normal_gaya(blok["jenis"], gabung)
        if blok.get("jenis") in ("figure", "table"):
            blok["text"] = blok["style"].get("caption", blok.get("text", ""))
    if "caption" in data and data["caption"] is not None and blok.get("jenis") in ("figure", "table"):
        blok["style"]["caption"] = str(data["caption"]).replace("\n", " ").strip()
        blok["text"] = blok["style"]["caption"]
    if "cells" in data and blok.get("jenis") == "table":
        edit = {}
        for c in data["cells"]:
            try:
                edit[int(c["index"])] = str(c.get("text", ""))
            except (KeyError, TypeError, ValueError):
                continue
        # perbarui model sel dan sumber tabel sekaligus (tanpa penanda internal)
        body_tabel = blok.get("table_body", "")
        for idx, teks in edit.items():
            if 0 <= idx < len(blok.get("cells", [])):
                blok["cells"][idx]["text"] = teks
        blok["table_body"] = ganti_sel_table(body_tabel, edit)
    if blok.get("jenis") == "meta":
        meta = terapkan_meta(dok, meta, {blok.get("meta_key")})
    if json.dumps(blok, sort_keys=True, ensure_ascii=False) == sebelum and "cells" not in data:
        hasil = ruang_kerja(folder, paksa_kompilasi=False)
        hasil["halaman_berubah"] = []
        return hasil
    body_baru = body if blok.get("jenis") == "meta" else render_body(body, dok)
    k = _terapkan_dan_kompilasi(folder, dok, body_baru, meta)
    if not k.get("ok"):
        return k
    # sinkronkan ulang model dari body hasil render
    dok = muat_dokumen(folder)
    _simpan_snapshot(folder, dok, baca_teks(os.path.join(folder, "body.typ")),
                     baca_json(os.path.join(folder, "meta.json"), {}), "Ubah %s" % bid)
    hasil = ruang_kerja(folder, paksa_kompilasi=False)
    hasil["halaman_berubah"] = _halaman_berubah(
        peta_lama, baca_json(os.path.join(folder, "layout_map.json"), {}) or {}, fokus=bid)
    # Judul/penulis juga muncul di header halaman ganjil, jadi semua halaman berubah
    # meskipun posisi blok di layout_map tidak bergeser.
    if blok.get("jenis") == "meta" and blok.get("meta_key") in ("judul", "penulis"):
        hasil["halaman_berubah"] = list(range(1, int(hasil.get("halaman") or 0) + 1))
    elif blok.get("jenis") == "meta":
        hasil["halaman_berubah"] = sorted(set(hasil.get("halaman_berubah") or []) | {1})
    return hasil


def _strip_typst_for_norm(s):
    """Normalisasi teks sumber Typst menjadi rangkaian karakter terlihat untuk pencocokan baris PDF."""
    s = re.sub(r"#(?:pm|pe|pf|h|v|metadata|label)\([^)]*\);?", "", s or "")
    s = re.sub(r"#(?:emph|strong|text|underline)\b(?:\([^)]*\))?\[", "", s)
    s = re.sub(r"#link\([^)]*\)\[", "", s)
    s = re.sub(r"\\([\[\]#$*_~`<>])", r"\1", s)
    s = s.replace("[", "").replace("]", "")
    s = s.replace("---", "—").replace("--", "–").replace("...", "…")
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = s.replace("ﬁ", "fi").replace("ﬂ", "fl")
    return re.sub(r"\s+", "", s)


def _imbang_kurung_typst(left_raw, right_raw):
    """Tutup tag [...] yang terbuka di ujung potongan kiri dan buka kembali di potongan kanan."""
    stack = []
    i = 0
    n = len(left_raw)
    while i < n:
        ch = left_raw[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "#" and i + 1 < n:
            m_tag = re.match(r"(#(?:emph|strong|text|underline|link)\b(?:\([^)]*\))?\[)", left_raw[i:])
            if m_tag:
                stack.append(m_tag.group(1))
                i += len(m_tag.group(1))
                continue
        if ch == "[":
            stack.append("[")
        elif ch == "]" and stack:
            stack.pop()
        i += 1
    if not stack:
        return left_raw, right_raw
    openers = [t for t in stack if t != "["]
    left_fixed = left_raw + ("]" * len(stack))
    right_fixed = ("".join(openers) if openers else ("[" * len(stack))) + right_raw
    return left_fixed, right_fixed


def _baris_kolom_pdf(pdf_path, hal, kol, y_start, y_end):
    """Ekstrak baris baseline PDF pada kolom & rentang Y tertentu."""
    try:
        import pymupdf as fitz  # noqa: E402
    except Exception:  # noqa: BLE001
        import fitz  # noqa: E402
    x0_min, x0_max = (70.0, 295.0) if kol == 1 else (298.0, 525.0)
    y_bot_limit = min(float(y_end) + 2.5, 765.0)
    with fitz.open(pdf_path) as doc:
        if hal < 1 or hal > len(doc):
            return []
        page = doc[hal - 1]
        words = [
            w for w in page.get_text("words")
            if x0_min <= w[0] <= x0_max and (float(y_start) + 4.0) <= w[3] <= y_bot_limit
        ]
    rows = []
    for w in sorted(words, key=lambda t: (round(t[3], 1), t[0])):
        if rows and abs(w[3] - rows[-1][0]) < 4.0:
            rows[-1][1].append(w[4])
        else:
            rows.append([w[3], [w[4]]])
    return rows


def _potong_teks_segmen_pdf(raw_isi, segs, pdf_path):
    """Belah string Typst paragraf lintas-kolom tepat di batas tiap segmen PDF."""
    if not raw_isi or not pdf_path or not os.path.isfile(pdf_path) or len(segs) < 2:
        return None
    segs_sorted = sorted(
        segs,
        key=lambda s: (int(s.get("page") or 0), 1 if float(s.get("x") or 0) < 296 else 2,
                       float(s.get("y") or 0), int(s.get("segment") or 0)),
    )
    distinct_cols = {(int(s.get("page") or 0), 1 if float(s.get("x") or 0) < 296 else 2) for s in segs_sorted}
    if len(distinct_cols) < 2:
        return None

    rem = raw_isi.strip()
    pieces = []
    for seg in segs_sorted[:-1]:
        hal = int(seg.get("page") or 0)
        kol = 1 if float(seg.get("x") or 0) < 296 else 2
        y_start = float(seg.get("y") or 0.0)
        y_end = y_start + float(seg.get("height") or 0.0)
        rows = _baris_kolom_pdf(pdf_path, hal, kol, y_start, y_end)
        if not rows:
            return None
        seg_txt = " ".join(" ".join(r[1]) for r in rows)
        k_len = len(_strip_typst_for_norm(seg_txt))
        last_pdf_word = _strip_typst_for_norm(rows[-1][1][-1]) if rows[-1][1] else ""

        depth_p = 0
        spaces = []
        i = 0
        while i < len(rem):
            ch = rem[i]
            if ch == "\\" and i + 1 < len(rem):
                i += 2
                continue
            if ch == "(":
                depth_p += 1
            elif ch == ")" and depth_p > 0:
                depth_p -= 1
            elif depth_p == 0 and ch.isspace():
                j = i
                while j < len(rem) and rem[j].isspace():
                    j += 1
                spaces.append((i, j))
                i = j
                continue
            i += 1

        cands = []
        for sa, sb in spaces:
            left_cand = rem[:sa]
            right_cand = rem[sb:]
            if not left_cand.strip() or not right_cand.strip():
                continue
            if re.fullmatch(r"(?:#h\([^)]*\)\s*)+", left_cand.strip()):
                continue
            p_norm = _strip_typst_for_norm(left_cand)
            dist = abs(len(p_norm) - k_len)
            bonus = -30 if (last_pdf_word and p_norm.endswith(last_pdf_word)) else 0
            cands.append((bonus + dist, dist, len(p_norm) > k_len, sa, sb))
        if not cands:
            return None
        cands.sort()
        _, _, _, sa, sb = cands[0]
        left_raw, right_raw = _imbang_kurung_typst(rem[:sa].strip(), rem[sb:].strip())
        if not left_raw or not right_raw:
            return None
        pieces.append(left_raw)
        rem = right_raw
    if not rem:
        return None
    pieces.append(rem)
    return pieces


def _isi_inti_render_blok(blk):
    """Ambil teks Typst inti paragraf dari render_teks tanpa #gaya-teks/#pm/#pe/#blk-akhir."""
    teks_render = _buang_penanda_akhir(render_teks(blk)).strip()
    mg = re.match(r"^(#gaya-teks\([^)]*\)\[)", teks_render)
    if mg and teks_render.endswith("]"):
        teks_render = teks_render[mg.end():-1].strip()
    teks_render = re.sub(r"^#pm\(\d+\);?", "", teks_render)
    teks_render = re.sub(r"\s*#pe\(\d+(?:,\s*bagian:\s*\d+)?\)\s*$", "", teks_render)
    return teks_render.strip()


def _siapkan_seg_pieces_dok(folder, dok, peta_lama, kandidat_ids=None, hasil_pdf=None):
    """Hitung seg_pieces untuk paragraf lintas-kolom yang digeser di mode Freeze."""
    if not dok or not isinstance(peta_lama, dict):
        return
    pdf_path = hasil_pdf or (os.path.join(folder, "hasil.pdf") if folder else None)
    segs_by_id = {}
    for seg in (peta_lama.get("blocks") or []):
        sid = seg.get("id")
        if sid:
            segs_by_id.setdefault(sid, []).append(seg)
    for b in dok.get("blocks", []):
        bid = b.get("id")
        if kandidat_ids is not None and bid not in kandidat_ids:
            continue
        src = b.setdefault("src", {})
        st = b.get("style", {}) or {}
        fx = angka(st.get("freeze_x"))
        fy = angka(st.get("freeze_y"))
        if abs(fx) <= 0.05 and abs(fy) <= 0.05:
            src.pop("seg_pieces", None)
            continue
        if b.get("jenis") != "paragraph" or st.get("break_before", "none") != "none":
            src.pop("seg_pieces", None)
            continue
        if isinstance(src.get("seg_pieces"), list) and len(src["seg_pieces"]) >= 2:
            continue
        segs = segs_by_id.get(bid, [])
        if len(segs) < 2 or not pdf_path or not os.path.isfile(pdf_path):
            continue
        if any(abs(angka(s.get("freeze_x"))) > 0.05 or abs(angka(s.get("freeze_y"))) > 0.05 for s in segs):
            continue
        raw_isi = _isi_inti_render_blok(b)
        pieces = _potong_teks_segmen_pdf(raw_isi, segs, pdf_path)
        if pieces:
            src["seg_pieces"] = pieces


def _id_pecahan_baru(anchor_id, blocks):
    m = re.match(r"^([a-z]+-\d+)", str(anchor_id or ""))
    dasar = m.group(1) if m else str(anchor_id or "p-001")
    ada = {b.get("id") for b in blocks if b.get("id")}
    for kode in range(ord("a"), ord("z") + 1):
        calon = "%s%s" % (dasar, chr(kode))
        if calon not in ada:
            return calon
    n = 1
    while True:
        calon = "%s-%d" % (dasar, n)
        if calon not in ada:
            return calon
        n += 1


def _potong_teks_baris_pdf_detail(lama, ratio, bid, peta_lama, folder):
    """Potong teks tepat pada batas akhir baris ke-k di PDF hasil render agar Zero-Reflow.

    Mengembalikan ``(kiri, kanan, meta_info)`` atau ``None``.
    """
    if not folder or not isinstance(peta_lama, dict):
        return None
    pdf_path = os.path.join(folder, "hasil.pdf")
    if not os.path.isfile(pdf_path):
        return None
    segs = [s for s in (peta_lama.get("blocks") or []) if s.get("id") == bid]
    if not segs:
        return None
    segs.sort(key=lambda s: (int(s.get("page") or 0), int(s.get("segment") or 0)))

    def _norm_ws(s):
        s = re.sub(r"\\([\[\]#$*_~`<>])", r"\1", s or "")
        s = s.replace("ﬁ", "fi").replace("ﬂ", "fl")
        return re.sub(r"\s+", "", s)

    lama_norm_len = len(_norm_ws(lama))
    try:
        import build  # noqa: E402
        by_page = {}
        pdf_lines = []
        seen = set()
        cum_norm_len = 0
        for s in segs:
            hal = int(s.get("page") or 0)
            if hal <= 0:
                continue
            if hal not in by_page:
                by_page[hal] = build.baris_narasi_terlihat(pdf_path, hal)
            vis_x = float(s.get("x") or 76.5)
            kol = (0 if vis_x < 290 else 1) if vis_x else (int(s.get("column") or 1) - 1)
            y_top = float(s.get("y") or 0.0) - 1.5
            y_bot = float(s.get("y") or 0.0) + float(s.get("height") or 0.0) + 1.5
            for ln in by_page[hal]:
                if ln.get("kol") != kol:
                    continue
                ly = ln["spans"][0]["origin"][1]
                if y_top <= ly <= y_bot:
                    sig = (hal, kol, round(ly, 2))
                    if sig in seen:
                        continue
                    seen.add(sig)
                    txt = "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
                    if not txt:
                        continue
                    # Cegah garis blok di bawahnya ikut terbawa bila kotak lama punya padding bawah berlebih
                    t_norm_len = len(_norm_ws(txt))
                    if pdf_lines and cum_norm_len >= lama_norm_len - 2 and (cum_norm_len + t_norm_len) > lama_norm_len + 8:
                        break
                    pdf_lines.append((hal, kol, ly, txt))
                    cum_norm_len += t_norm_len
    except Exception:  # noqa: BLE001
        return None
    if len(pdf_lines) < 2:
        return None
    k = max(1, min(len(pdf_lines) - 1, int(round(len(pdf_lines) * ratio))))

    k_len = len(_norm_ws("".join(item[3] for item in pdf_lines[:k])))
    last_pdf_word = _norm_ws(pdf_lines[k - 1][3].split()[-1]) if pdf_lines[k - 1][3].split() else ""
    first_next_word = _norm_ws(pdf_lines[k][3].split()[0]) if pdf_lines[k][3].split() else ""
    candidates = []
    exact_hit = False
    for m in re.finditer(r"\s+", lama):
        if m.start() <= 0 or m.end() >= len(lama):
            continue
        p_norm = _norm_ws(lama[:m.start()])
        dist = abs(len(p_norm) - k_len)
        bonus = 0
        hit_left = bool(last_pdf_word and p_norm.endswith(last_pdf_word))
        r_norm = _norm_ws(lama[m.end():])
        hit_right = bool(first_next_word and r_norm.startswith(first_next_word))
        if hit_left:
            bonus -= 20
        if hit_right:
            bonus -= 20
        if hit_left and hit_right:
            exact_hit = True
        candidates.append((bonus, dist, len(p_norm) > k_len, m.start(), m.end()))
    if candidates:
        if exact_hit:
            candidates.sort(key=lambda c: (c[0] + c[1], c[1]))
        else:
            # Bila baris ke-k putus di tengah kata (hyphenation), pilih spasi SEBELUM kata tersebut
            # agar bagian pertama tidak meluap +1 baris ke baris berikutnya.
            candidates.sort(key=lambda c: (c[0] + c[1] + (15 if c[2] else 0), c[1]))
        _, _, _, s_awal, s_akhir = candidates[0]
        kiri = lama[:s_awal].strip()
        kanan = lama[s_akhir:].strip()
        if kiri and kanan:
            hal_k, kol_k, base_k, _ = pdf_lines[k - 1]
            return kiri, kanan, {
                "hal_k": hal_k, "kol_k": kol_k, "base_k": base_k,
                "k_lines": k, "total_lines": len(pdf_lines),
            }
    return None


def _potong_teks_baris_pdf(lama, ratio, bid, peta_lama, folder):
    res = _potong_teks_baris_pdf_detail(lama, ratio, bid, peta_lama, folder)
    if not res:
        return None
    return res[0], res[1]


def _pecah_blok_memori(dok, item, peta_lama=None, folder=None):
    """Pecah satu blok teks di memori menjadi blok jangkar (kiri) + pecahan baru (kanan)."""
    bid = str(item.get("block_id") or item.get("after_id") or item.get("id") or "")
    blocks = dok.get("blocks", [])
    anchor = next((b for b in blocks if b.get("id") == bid), None)
    if not anchor or anchor.get("jenis") in ("meta", "figure", "table"):
        return False, None, "Blok %s tidak dapat dipecah" % (bid or "?")

    orig_pm = (anchor.get("src") or {}).get("pm")
    st_lama = dict(anchor.get("style") or {})
    is_frozen = (
        bool(item.get("frozen"))
        if item.get("frozen") is not None
        else (
            bool(st_lama.get("frozen"))
            or bool(st_lama.get("unlocked_in_freeze"))
            or abs(angka(st_lama.get("freeze_x"))) > 0.05
            or abs(angka(st_lama.get("freeze_y"))) > 0.05
        )
    )

    tb = str(item.get("text_before") or "").strip()
    ta = str(item.get("text_after") or "").strip()
    potongan_meta = None
    if tb and ta:
        kiri, kanan = tb, ta
    else:
        lama = (anchor.get("text") or "").strip()
        if len(lama) < 2:
            return False, None, "Teks blok %s terlalu pendek untuk dipecah" % bid
        ratio_raw = item.get("split_ratio")
        ratio = None
        if ratio_raw is not None and ratio_raw != "":
            try:
                ratio = float(ratio_raw)
            except (TypeError, ValueError):
                ratio = None
        potongan_pdf = None
        if ratio is not None:
            ratio = max(0.01, min(0.99, ratio))
            if is_frozen:
                potongan_pdf = _potong_teks_baris_pdf_detail(lama, ratio, bid, peta_lama, folder)
        if potongan_pdf:
            kiri, kanan, potongan_meta = potongan_pdf
        else:
            if ratio is not None:
                offset = max(1, min(len(lama) - 1, int(round(len(lama) * ratio))))
            else:
                offset = item.get("split_offset")
                try:
                    offset = int(offset) if offset is not None else None
                except (TypeError, ValueError):
                    offset = None
                if offset is None or offset < 1 or offset >= len(lama):
                    offset = len(lama) // 2
            if not (lama[offset - 1].isspace() or lama[offset].isspace()):
                spasi = [(min(abs(offset - m.start()), abs(offset - m.end())), m.start(), m.end())
                         for m in re.finditer(r"\s+", lama)
                         if m.start() > 0 and m.end() < len(lama)]
                if spasi:
                    spasi.sort(key=lambda x: x[0])
                    _, s_awal, s_akhir = spasi[0]
                    kiri = lama[:s_awal].strip()
                    kanan = lama[s_akhir:].strip()
                else:
                    kiri = lama[:offset].strip()
                    kanan = lama[offset:].strip()
            else:
                kiri = lama[:offset].strip()
                kanan = lama[offset:].strip()
        if not kiri or not kanan:
            return False, None, "Gagal menentukan titik potong teks"

    m_h = re.match(r"^(#h\([^)]+\)\s*)", str(anchor.get("content") or "").lstrip())
    prefix_h = m_h.group(1) if m_h else ""
    anchor["text"] = kiri
    anchor["content"] = prefix_h + typst_dari_teks_ui(kiri)

    new_id = _id_pecahan_baru(bid, blocks)
    maks_pm = max([int(b.get("src", {}).get("pm") or 0) for b in blocks
                   if b.get("jenis") not in ("meta", "figure", "table")] + [0])
    new_pm = maks_pm + 1

    src_anchor = dict(anchor.get("src") or {})
    if anchor.get("jenis") == "heading":
        jenis_baru = "paragraph"
        tipe_baru = "paragraph"
        judul_baru = "Paragraf"
        form_baru = "paragraph"
        prefix_baru = ""
        level_baru = None
    else:
        jenis_baru = anchor.get("jenis", "paragraph")
        tipe_baru = anchor.get("tipe") or _tipe_teks(jenis_baru)
        judul_baru = anchor.get("judul") or _label_blok({"jenis": jenis_baru, "level": anchor.get("level")})
        form_baru = src_anchor.get("form") or jenis_baru
        prefix_baru = src_anchor.get("prefix") or ""
        level_baru = anchor.get("level")

    fx_base = round(clamp(angka(item.get("freeze_x"), st_lama.get("freeze_x", 0.0)), -500.0, 500.0), 3) \
        if item.get("freeze_x") is not None else st_lama.get("freeze_x", 0.0)
    fy_base = round(clamp(angka(item.get("freeze_y"), st_lama.get("freeze_y", 0.0)), -800.0, 800.0), 3) \
        if item.get("freeze_y") is not None else st_lama.get("freeze_y", 0.0)

    src_anchor.pop("seg_pieces", None)
    st_anchor = dict(st_lama)
    st_anchor["spacing_below"] = 0.0
    if is_frozen:
        st_anchor["frozen"] = True
        st_anchor["unlocked_in_freeze"] = False
        st_anchor["freeze_x"] = fx_base
        st_anchor["freeze_y"] = fy_base
    anchor["style"] = normal_gaya(anchor.get("tipe") or anchor.get("jenis", "paragraph"), st_anchor)

    st_baru = dict(st_lama)
    st_baru["spacing_above"] = 0.0
    st_baru["keep_with_next"] = False
    use_colbreak = bool(item.get("colbreak", False))
    if use_colbreak:
        st_baru["break_before"] = "column"
        st_baru["freeze_x"] = 0.0
        st_baru["freeze_y"] = 0.0
        st_baru["no_indent"] = False
    else:
        st_baru["break_before"] = "none"
        st_baru["freeze_x"] = fx_base if is_frozen else st_lama.get("freeze_x", 0.0)
        st_baru["freeze_y"] = fy_base if is_frozen else st_lama.get("freeze_y", 0.0)
        st_baru["no_indent"] = bool(is_frozen)
    if is_frozen:
        st_baru["frozen"] = True
        st_baru["unlocked_in_freeze"] = False
    elif item.get("frozen") is not None:
        st_baru["frozen"] = False
        if bool(st_lama.get("unlocked_in_freeze")):
            st_baru["unlocked_in_freeze"] = True
    style_baru = normal_gaya(tipe_baru, st_baru)

    blok_baru = {
        "jenis": jenis_baru,
        "tipe": tipe_baru,
        "judul": judul_baru,
        "level": level_baru,
        "text": kanan,
        "content": typst_dari_teks_ui(kanan),
        "style": style_baru,
        "id": new_id,
        "src": {"kind": "text", "pm": new_pm, "form": form_baru, "prefix": prefix_baru,
                "baru": True, "insert_after": bid},
    }
    pos = blocks.index(anchor) + 1
    blocks.insert(pos, blok_baru)
    return True, {
        "source_id": bid,
        "new_id": new_id,
        "new_pm": new_pm,
        "orig_pm": orig_pm,
        "frozen": bool(is_frozen),
        "colbreak": bool(use_colbreak),
        "fx_base": fx_base,
        "fy_base": fy_base,
        "pdf_meta": potongan_meta,
    }, None


def _kalibrasi_pecah_beku(folder, body, dok, hasil_split, multi_seg_ids, body_pre_split):
    """Pastikan pecahan beku di kolom yang sama Zero-Reflow 0pt di Typst."""
    frozen_splits = [
        sp for sp in (hasil_split or [])
        if isinstance(sp, dict) and sp.get("frozen") and not sp.get("colbreak")
    ]
    main_typ = os.path.join(folder, "main.typ")
    body_path = os.path.join(folder, "body.typ")
    if not frozen_splits or not body_pre_split or not os.path.isfile(main_typ) or not os.path.isfile(body_path):
        return

    body_disk = baca_teks(body_path)
    try:
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(body_pre_split)
        pos_sebelum, err0 = _kueri_posisi_typst(main_typ)
        if not pos_sebelum:
            return

        # Hapus penanda sementara baru lalu kembalikan setelah selesai? render_body
        # memakai flag src.baru/insert_after, jadi snapshot dok tetap aman selama belum pop().
        def _query_cur():
            cand = render_body(body, dok, multi_seg_ids=multi_seg_ids)
            with open(body_path, "w", encoding="utf-8") as f:
                f.write(cand)
            p_cur, _ = _kueri_posisi_typst(main_typ)
            return p_cur

        blok_map = {b.get("id"): b for b in dok.get("blocks", [])}
        pos_cur = _query_cur()
        if not pos_cur:
            return

        # Daftar penanda setelah pecahan pertama untuk mengecek pergeseran global
        first_idx = min([dok["blocks"].index(blok_map[sp["source_id"]]) for sp in frozen_splits
                         if sp["source_id"] in blok_map] or [len(dok["blocks"])])
        pms_sesudah = []
        for idx_b in range(first_idx + 1, len(dok["blocks"])):
            bk = dok["blocks"][idx_b]
            pm_num = (bk.get("src") or {}).get("pm")
            if pm_num and ("pm-%s" % pm_num) in pos_sebelum:
                pms_sesudah.append("pm-%s" % pm_num)

        split_moved_flags = {}
        for sp in frozen_splits:
            fx_b = angka(sp.get("fx_base"), 0.0)
            fy_b = angka(sp.get("fy_base"), 0.0)
            b1_s = (blok_map.get(sp.get("source_id")) or {}).get("style") or {}
            b2_s = (blok_map.get(sp.get("new_id")) or {}).get("style") or {}
            m1 = abs(angka(b1_s.get("freeze_x")) - fx_b) > 0.05 or abs(angka(b1_s.get("freeze_y")) - fy_b) > 0.05
            m2 = abs(angka(b2_s.get("freeze_x")) - fx_b) > 0.05 or abs(angka(b2_s.get("freeze_y")) - fy_b) > 0.05
            split_moved_flags[sp.get("source_id")] = (m1, m2)
        any_split_moved = any(m1 or m2 for m1, m2 in split_moved_flags.values())

        def _hitung_geser(p_now):
            if not p_now:
                return 10**9
            tot = 0.0
            if not any_split_moved:
                for pk in pms_sesudah:
                    v0 = pos_sebelum.get(pk)
                    v1 = p_now.get(pk)
                    if not v0 or not v1:
                        tot += 1000.0
                    elif v0[1] != v1[1]:
                        tot += 500.0 + abs(v0[3] - v1[3])
                    elif abs(v0[3] - v1[3]) > 0.15:
                        tot += abs(v0[3] - v1[3])
            for sp in frozen_splits:
                opm = sp.get("orig_pm")
                npm = sp.get("new_pm")
                m1, m2 = split_moved_flags.get(sp.get("source_id"), (False, False))
                if m1 or m2:
                    continue
                fa0 = pos_sebelum.get("frag-akhir-%s-0" % opm) if opm else None
                fa2 = p_now.get("frag-akhir-%s-0" % npm) if npm else None
                if fa0 and fa2:
                    if fa0[1] != fa2[1]:
                        tot += 500.0
                    elif abs(fa0[3] - fa2[3]) > 0.5:
                        tot += abs(fa0[3] - fa2[3])
            return round(tot, 3)

        if _hitung_geser(pos_cur) == 0.0:
            return

        for sp in frozen_splits:
            b1 = blok_map.get(sp["source_id"])
            b2 = blok_map.get(sp["new_id"])
            if not b1 or not b2:
                continue
            m1, m2 = split_moved_flags.get(sp.get("source_id"), (False, False))
            if m1 and m2:
                continue
            b1_pm = (b1.get("src") or {}).get("pm")
            b2_pm = (b2.get("src") or {}).get("pm")
            opm = sp.get("orig_pm")
            fa0 = pos_sebelum.get("frag-akhir-%s-0" % opm) if opm else None
            pmeta = sp.get("pdf_meta") or {}
            base_t1 = angka(b1.get("style", {}).get("tracking"), 0.0)
            base_t2 = angka(b2.get("style", {}).get("tracking"), 0.0)

            # 1) Kalibrasi blok pertama b1 agar berakhir tepat di baris ke-k
            fa1 = pos_cur.get("frag-akhir-%s-0" % b1_pm) if b1_pm else None
            hal_k = pmeta.get("hal_k")
            base_k = pmeta.get("base_k")
            if not m1 and fa1 and hal_k is not None and base_k is not None:
                diff1 = (fa1[1] - hal_k) * 1000.0 + (fa1[3] - base_k)
                if abs(diff1) > 2.5:
                    steps = (-0.05, -0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40) if diff1 > 0 else (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
                    best_t1, best_err1 = base_t1, abs(diff1)
                    for dt in steps:
                        b1["style"]["tracking"] = round(clamp(base_t1 + dt, *BATAS_TRACKING), 3)
                        p_try = _query_cur()
                        fa1_t = p_try.get("frag-akhir-%s-0" % b1_pm) if p_try else None
                        if fa1_t:
                            d_t = abs((fa1_t[1] - hal_k) * 1000.0 + (fa1_t[3] - base_k))
                            if d_t < best_err1:
                                best_err1 = d_t
                                best_t1 = b1["style"]["tracking"]
                            if d_t <= 2.5:
                                pos_cur = p_try
                                break
                    else:
                        b1["style"]["tracking"] = best_t1
                        pos_cur = _query_cur()

            # 2) Kalibrasi blok kedua b2 (atau b1 fallback) agar sisa tinggi tepat nol-reflow
            if not m2 and _hitung_geser(pos_cur) > 0.0:
                fa2 = pos_cur.get("frag-akhir-%s-0" % b2_pm) if b2_pm else None
                diff2 = ((fa2[1] - fa0[1]) * 1000.0 + (fa2[3] - fa0[3])) if (fa0 and fa2) else 11.0
                steps2 = (-0.05, -0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40) if diff2 >= -0.5 else (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
                best_t1 = angka(b1["style"].get("tracking"), 0.0)
                best_t2 = base_t2
                best_score = _hitung_geser(pos_cur)
                for dt in steps2:
                    b2["style"]["tracking"] = round(clamp(base_t2 + dt, *BATAS_TRACKING), 3)
                    p_try = _query_cur()
                    sc = _hitung_geser(p_try)
                    if sc < best_score:
                        best_score = sc
                        best_t2 = b2["style"]["tracking"]
                    if sc == 0.0:
                        pos_cur = p_try
                        break
                else:
                    b2["style"]["tracking"] = best_t2
                    # Bila b1 belum cocok karena pmeta tidak tersedia, coba juga tracking pada b1
                    if not m1 and best_score > 0.0 and hal_k is None:
                        for dt1 in (-0.05, -0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40, 0.05, 0.10, 0.15, 0.20):
                            b1["style"]["tracking"] = round(clamp(base_t1 + dt1, *BATAS_TRACKING), 3)
                            b2["style"]["tracking"] = base_t2
                            p_try = _query_cur()
                            sc = _hitung_geser(p_try)
                            if sc < best_score:
                                best_score = sc
                                best_t1 = b1["style"]["tracking"]
                                best_t2 = base_t2
                            if sc == 0.0:
                                pos_cur = p_try
                                break
                        else:
                            b1["style"]["tracking"] = best_t1
                            b2["style"]["tracking"] = best_t2
                    pos_cur = _query_cur()
    finally:
        try:
            with open(body_path, "w", encoding="utf-8") as f:
                f.write(body_disk)
        except OSError:
            pass


def aksi_insert(folder, data):
    folder = os.path.abspath(folder)
    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    peta_lama = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}
    wants_split = (data.get("split_ratio") is not None) or (data.get("split_offset") is not None) or bool(str(data.get("text_before") or "").strip())
    split_info = None
    dipecah_ids = set()
    if wants_split:
        ok_sp, split_info, galat_sp = _pecah_blok_memori(dok, data, peta_lama=peta_lama, folder=folder)
        if not ok_sp:
            return {"ok": False, "galat": galat_sp or "Gagal memecah blok"}
        bid = split_info["new_id"]
        dipecah_ids.add(split_info["source_id"])
    else:
        after = str(data.get("after_id") or data.get("block_id") or "")
        anchor = next((b for b in dok["blocks"] if b.get("id") == after), None)
        if not anchor or anchor.get("jenis") in ("meta", "figure", "table"):
            return {"ok": False, "galat": "Blok jangkar tidak dapat disisipi paragraf"}
        teks = str(data.get("text", "") or "")
        maks = max([int(b["src"].get("pm", 0) or 0) for b in dok["blocks"]
                    if b.get("jenis") not in ("meta", "figure", "table")] + [0])
        n = maks + 1
        hitung = sum(1 for b in dok["blocks"] if b.get("jenis") == "paragraph") + 1
        bid = "p-%03d" % hitung
        while any(b.get("id") == bid for b in dok["blocks"]):
            hitung += 1
            bid = "p-%03d" % hitung
        blok = {"jenis": "paragraph", "tipe": "paragraph", "judul": "Paragraf",
                "text": teks or "Paragraf baru.", "content": typst_dari_teks_ui(teks or "Paragraf baru."),
                "style": normal_gaya("paragraph", {}), "id": bid,
                "src": {"kind": "text", "pm": n, "form": "paragraph", "prefix": "",
                        "baru": True, "insert_after": after}}
        pos = dok["blocks"].index(anchor) + 1
        dok["blocks"].insert(pos, blok)

    hitung_seg = {}
    for seg in (peta_lama.get("blocks") or []):
        sid = seg.get("id")
        if sid:
            hitung_seg[sid] = hitung_seg.get(sid, 0) + 1
    multi_seg_ids = {sid for sid, cnt in hitung_seg.items() if cnt > 1} - dipecah_ids

    body_baru = render_body(body, dok, multi_seg_ids=multi_seg_ids)
    # blok baru sudah tertulis di body; hapus flag agar tidak ditulis dua kali
    for b in dok.get("blocks", []):
        src = b.get("src", {})
        src.pop("baru", None)
        src.pop("insert_after", None)
    k = _terapkan_dan_kompilasi(folder, dok, body_baru, meta)
    if not k.get("ok"):
        return k
    simpan_dokumen(folder, dok)
    dok = muat_dokumen(folder)
    _simpan_snapshot(folder, dok, baca_teks(os.path.join(folder, "body.typ")),
                     baca_json(os.path.join(folder, "meta.json"), {}), "Sisip blok %s" % bid)
    hasil = ruang_kerja(folder, paksa_kompilasi=False)
    if split_info:
        hasil["split"] = split_info
    return hasil


def aksi_delete(folder, data):
    folder = os.path.abspath(folder)
    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    bid = str(data.get("block_id", ""))
    blok = next((b for b in dok["blocks"] if b.get("id") == bid), None)
    if not blok:
        return {"ok": False, "galat": "Blok %s tidak ditemukan" % bid}
    if blok.get("jenis") == "meta":
        return {"ok": False, "galat": "Blok metadata tidak dapat dihapus"}
    if blok.get("jenis") in ("figure", "table"):
        dok.setdefault("dihapus_objek", []).append(blok.get("nomor"))
    else:
        dok.setdefault("dihapus_pm", []).append(blok.get("src", {}).get("pm"))
    dok["blocks"] = [b for b in dok["blocks"] if b.get("id") != bid]
    body_baru = render_body(body, dok)
    k = _terapkan_dan_kompilasi(folder, dok, body_baru, meta)
    if not k.get("ok"):
        return k
    # baris sudah terhapus dari body; bersihkan daftar agar tidak menumpuk
    dok.pop("dihapus_pm", None)
    dok.pop("dihapus_objek", None)
    simpan_dokumen(folder, dok)
    dok = muat_dokumen(folder)
    _simpan_snapshot(folder, dok, baca_teks(os.path.join(folder, "body.typ")),
                     baca_json(os.path.join(folder, "meta.json"), {}), "Hapus %s" % bid)
    return ruang_kerja(folder, paksa_kompilasi=False)


def aksi_move(folder, data):
    """Aksi CLI/API: pindahkan blok sesuai payload JSON."""
    ids = data.get("block_ids")
    if not ids:
        ids = data.get("source_block_ids") or []
    return pindahkan_blok(folder, ids,
                          data.get("target_id") or data.get("target_block_id"),
                          data.get("posisi") or data.get("position"),
                          data.get("placement"))


def aksi_batch(folder, data):
    """Ubah gaya dan/atau hapus banyak blok dengan SATU kompilasi Typst.

    Bentuk data yang didukung:
      {"changes": [{"block_id": "...", "style": {...}}, ...]}
      {"delete_ids": ["...", ...]}
      {"block_ids": [...], "style": {...}}                 # patch gaya yang sama
      {"block_ids": [...], "hapus": true}                  # hapus banyak id
    """
    folder = os.path.abspath(folder)
    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    peta_lama = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}

    perubahan = data.get("changes") if isinstance(data.get("changes"), list) else []
    hapus_ids = data.get("delete_ids") if isinstance(data.get("delete_ids"), list) else []
    bersama = data.get("block_ids") if isinstance(data.get("block_ids"), list) else []
    patch_bersama = data.get("style") if isinstance(data.get("style"), dict) else None
    if bersama and patch_bersama:
        perubahan = list(perubahan) + [{"block_id": x, "style": patch_bersama} for x in bersama]
    if bersama and (data.get("hapus") or data.get("mode") == "delete"):
        hapus_ids = list(hapus_ids) + bersama
    if len(perubahan) > 1000 or len(hapus_ids) > 1000:
        return {"ok": False, "galat": "Terlalu banyak blok dalam satu permintaan (maks. 1000)"}

    indeks = {b.get("id"): b for b in dok.get("blocks", [])}
    dilewati = []
    diperbarui = []
    berubah = False

    for c in perubahan:
        if not isinstance(c, dict):
            continue
        bid = str(c.get("block_id") or c.get("id") or "")
        blok = indeks.get(bid)
        if not blok:
            dilewati.append({"block_id": bid, "alasan": "tidak ditemukan"})
            continue
        patch = c.get("style")
        if not isinstance(patch, dict) or not patch:
            continue
        if blok.get("jenis") == "meta":
            dilewati.append({"block_id": bid, "alasan": "metadata tidak memiliki gaya blok"})
            continue
        sebelum = json.dumps(blok, sort_keys=True, ensure_ascii=False)
        gabung = dict(blok.get("style", {}))
        gabung.update(patch)
        blok["style"] = normal_gaya(blok.get("jenis"), gabung)
        if blok.get("jenis") in ("figure", "table"):
            blok["text"] = blok["style"].get("caption", blok.get("text", ""))
        if json.dumps(blok, sort_keys=True, ensure_ascii=False) != sebelum:
            berubah = True
            if bid not in diperbarui:
                diperbarui.append(bid)

    hapus = []
    sudah = set()
    for raw in hapus_ids:
        bid = str(raw)
        if bid in sudah:
            continue
        blok = indeks.get(bid)
        if not blok:
            dilewati.append({"block_id": bid, "alasan": "tidak ditemukan"})
            continue
        if blok.get("jenis") == "meta":
            dilewati.append({"block_id": bid, "alasan": "metadata tidak dapat dihapus"})
            continue
        sudah.add(bid)
        if blok.get("jenis") in ("figure", "table"):
            dok.setdefault("dihapus_objek", []).append(blok.get("nomor"))
        else:
            dok.setdefault("dihapus_pm", []).append(blok.get("src", {}).get("pm"))
        hapus.append(bid)
    if hapus:
        hapus_set = set(hapus)
        dok["blocks"] = [b for b in dok.get("blocks", []) if b.get("id") not in hapus_set]
        berubah = True

    if not berubah:
        hasil = ruang_kerja(folder, paksa_kompilasi=False)
        hasil.update({"halaman_berubah": [], "diperbarui": [], "dihapus": [], "dilewati": dilewati})
        return hasil

    body_baru = render_body(body, dok)
    k = _terapkan_dan_kompilasi(folder, dok, body_baru, meta)
    if not k.get("ok"):
        return k
    # Baris sudah terhapus dari body; bersihkan penanda agar tidak menumpuk.
    dok.pop("dihapus_pm", None)
    dok.pop("dihapus_objek", None)
    simpan_dokumen(folder, dok)
    dok = muat_dokumen(folder)
    bagian = []
    if diperbarui:
        bagian.append("%d diubah" % len(diperbarui))
    if hapus:
        bagian.append("%d dihapus" % len(hapus))
    _simpan_snapshot(folder, dok, baca_teks(os.path.join(folder, "body.typ")),
                     baca_json(os.path.join(folder, "meta.json"), {}), "Batch: " + ", ".join(bagian))
    hasil = ruang_kerja(folder, paksa_kompilasi=False)
    hasil["halaman_berubah"] = _halaman_berubah(
        peta_lama, baca_json(os.path.join(folder, "layout_map.json"), {}) or {})
    hasil["diperbarui"] = diperbarui
    hasil["dihapus"] = hapus
    hasil["dilewati"] = dilewati
    return hasil


def _terapkan_edit_memori(blok, data):
    """Terapkan field editable dari draft ke satu blok (tanpa kompilasi)."""
    sebelum = json.dumps(blok, sort_keys=True, ensure_ascii=False)
    jenis = blok.get("jenis")
    if "text" in data and data["text"] is not None:
        teks = str(data["text"])
        # Jangan render ulang konten Typst bila teks tidak berubah (mis. edit
        # hanya gaya/ukuran): escape dan markup lama akan tetap utuh.
        if teks != str(blok.get("text") or ""):
            if jenis == "meta":
                blok["text"] = teks
                blok["content"] = teks
            elif jenis in ("figure", "table"):
                st = normal_gaya(jenis, blok.get("style"))
                st["caption"] = teks.replace("\n", " ").strip()
                blok["style"] = st
                blok["text"] = st["caption"]
            else:
                blok["content"] = typst_dari_teks_ui(teks)
                blok["text"] = teks
    has_style = isinstance(data.get("style"), dict)
    has_root_voff = "vertical_offset" in data and data["vertical_offset"] is not None
    has_freeze = ("freeze_x" in data or "freeze_y" in data or "frozen" in data or "unlocked_in_freeze" in data or
                  (has_style and ("freeze_x" in data["style"] or "freeze_y" in data["style"] or
                                  "frozen" in data["style"] or "unlocked_in_freeze" in data["style"])))
    if jenis != "meta" and (has_style or has_root_voff or has_freeze):
        gabung = dict(blok.get("style", {}))
        if has_style:
            gabung.update(data["style"])
            if "spacing_above" not in data["style"] and "vertical_offset" in data["style"]:
                gabung["spacing_above"] = data["style"]["vertical_offset"]
        if has_root_voff and not (has_style and "spacing_above" in data["style"]):
            gabung["spacing_above"] = data["vertical_offset"]
        if "frozen" in data and (not has_style or "frozen" not in data["style"]):
            gabung["frozen"] = data["frozen"]
        if "unlocked_in_freeze" in data and (not has_style or "unlocked_in_freeze" not in data["style"]):
            gabung["unlocked_in_freeze"] = data["unlocked_in_freeze"]
        if "freeze_x" in data and (not has_style or "freeze_x" not in data["style"]):
            gabung["freeze_x"] = data["freeze_x"]
        if "freeze_y" in data and (not has_style or "freeze_y" not in data["style"]):
            gabung["freeze_y"] = data["freeze_y"]
        blok["style"] = normal_gaya(jenis, gabung)
        if jenis in ("figure", "table"):
            blok["text"] = blok["style"].get("caption", blok.get("text", ""))
    if "caption" in data and data["caption"] is not None and jenis in ("figure", "table"):
        blok.setdefault("style", {})["caption"] = str(data["caption"]).replace("\n", " ").strip()
        blok["text"] = blok["style"]["caption"]
    if "cells" in data and jenis == "table" and isinstance(data["cells"], list):
        edit = {}
        for c in data["cells"]:
            try:
                edit[int(c["index"])] = str(c.get("text", ""))
            except (KeyError, TypeError, ValueError):
                continue
        body_tabel = blok.get("table_body", "")
        for idx, teks in edit.items():
            if 0 <= idx < len(blok.get("cells", [])):
                blok["cells"][idx]["text"] = teks
        blok["table_body"] = ganti_sel_table(body_tabel, edit)
    return json.dumps(blok, sort_keys=True, ensure_ascii=False) != sebelum


def _hapus_memori(dok, ids):
    """Hapus blok dari ``dok`` di memori tanpa kompilasi. Kembalikan (hapus, dilewati)."""
    blok_map = {b.get("id"): b for b in dok.get("blocks", [])}
    hapus, dilewati, sudah = [], [], set()
    for raw in ids:
        bid = str(raw)
        if not bid or bid in sudah:
            continue
        blok = blok_map.get(bid)
        if not blok:
            dilewati.append(bid)
            continue
        if blok.get("jenis") == "meta":
            dilewati.append(bid)
            continue
        sudah.add(bid)
        if blok.get("jenis") in ("figure", "table"):
            dok.setdefault("dihapus_objek", []).append(blok.get("nomor"))
        else:
            dok.setdefault("dihapus_pm", []).append(blok.get("src", {}).get("pm"))
        hapus.append(bid)
    if hapus:
        hapus_set = set(hapus)
        dok["blocks"] = [b for b in dok.get("blocks", []) if b.get("id") not in hapus_set]
    return hapus, dilewati


def _terapkan_crops_memori(dok, crops):
    if not isinstance(crops, list) or not crops:
        return 0
    diubah = 0
    for item in crops:
        if not isinstance(item, dict):
            continue
        bid = str(item.get("block_id") or item.get("id") or "")
        nomor = item.get("nomor")
        item_jenis = item.get("jenis")
        blok = None
        if bid:
            for b in dok.get("blocks", []):
                if b.get("jenis") in ("figure", "table") and b.get("id") == bid:
                    blok = b
                    break
        if not blok and nomor is not None:
            kandidat = []
            for b in dok.get("blocks", []):
                if b.get("jenis") in ("figure", "table") and str(b.get("nomor")) == str(nomor):
                    kandidat.append(b)
            if kandidat:
                if item_jenis:
                    blok = next((b for b in kandidat if b.get("jenis") == item_jenis), kandidat[0])
                else:
                    blok = kandidat[0]
        if not blok:
            continue
        st = dict(blok.get("style") or {})
        lama = json.dumps(st, sort_keys=True, ensure_ascii=False)
        if item.get("reset") or item.get("crop") is None and all(k not in item for k in ("left", "top", "right", "bottom")):
            st["crop"] = None
        else:
            src_c = item.get("crop") if isinstance(item.get("crop"), dict) else item
            st["crop"] = {
                "left": angka(src_c.get("left"), 0.0),
                "top": angka(src_c.get("top"), 0.0),
                "right": angka(src_c.get("right"), 0.0),
                "bottom": angka(src_c.get("bottom"), 0.0),
            }
        st_baru = normal_gaya(blok.get("jenis"), st)
        if json.dumps(st_baru, sort_keys=True, ensure_ascii=False) != lama:
            blok["style"] = st_baru
            diubah += 1
    return diubah


def _terapkan_batas_hal_body(body, perubahan_map, direktif):
    if not isinstance(perubahan_map, dict) or not perubahan_map:
        return body, False
    kepala = '#import "/template_jurnal.typ": *\n'
    if not (body or "").startswith(kepala):
        return body, False
    peta = _baca_batas_hal_body(body, direktif)
    berubah = False
    for k, v in perubahan_map.items():
        try:
            hal = int(k)
            baris = max(-2, min(2, int(round(float(v or 0)))))
        except (TypeError, ValueError):
            continue
        if hal <= 0:
            continue
        lama = peta.get(str(hal), 0)
        if baris != lama:
            berubah = True
            if baris != 0:
                peta[str(hal)] = baris
            else:
                peta.pop(str(hal), None)
    if not berubah:
        return body, False
    pola_hapus = r"^#%s\(\d+,\s*-?[\d.]+pt\)\n?" % re.escape(direktif)
    tanpa_dir = re.sub(pola_hapus, "", body[len(kepala):], flags=re.M)
    baris_dir = "".join("#%s(%d, %.1fpt)\n" % (direktif, int(h), peta[h] * 11.0)
                        for h in sorted(peta, key=lambda x: int(x)))
    return kepala + baris_dir + tanpa_dir, True


def _terapkan_kaki_body(body, kaki_naik):
    return _terapkan_batas_hal_body(body, kaki_naik, "kaki-naik")


def _terapkan_kepala_body(body, header_naik):
    return _terapkan_batas_hal_body(body, header_naik, "kepala-turun")


def edit_metadata_saja(edits, moves, deletes, dok, crops=None, kaki_naik=None, header_naik=None, header_shift=None, footer_shift=None, splits=None):
    if moves or deletes or splits or not edits or crops or kaki_naik or header_naik or header_shift is not None or footer_shift is not None:
        return False
    indeks = {b.get("id"): b for b in dok.get("blocks", [])}
    return all(isinstance(e, dict) and indeks.get(str(e.get("block_id") or e.get("id") or ""), {}).get("jenis") == "meta"
               for e in edits)


def kunci_edit_metadata(edits, dok):
    ids = {str(e.get("block_id") or e.get("id") or "") for e in edits if isinstance(e, dict)}
    return {b.get("meta_key") for b in dok.get("blocks", [])
            if b.get("jenis") == "meta" and b.get("id") in ids}


_FREEZE_DEFER_KEYS = ("frozen", "unlocked_in_freeze", "freeze_x", "freeze_y")


def _terapkan_prapindah_split_segmen(folder, body, dok, meta, multi_seg_ids, deferred_edits, hasil_split, hitung_seg):
    """Bila belahan dari paragraf lintas-kolom ikut digeser, petakan segmen pasca-split sebelum geser."""
    split_sources = {sp.get("source_id"): sp for sp in (hasil_split or []) if isinstance(sp, dict)}
    split_new_to_src = {sp.get("new_id"): sp.get("source_id") for sp in (hasil_split or []) if isinstance(sp, dict)}
    moved_split_ids = set()
    for e in deferred_edits:
        bid = str(e.get("block_id") or e.get("id") or "")
        st_e = e.get("style") if isinstance(e.get("style"), dict) else {}
        src_id = bid if bid in split_sources else split_new_to_src.get(bid)
        if not src_id or hitung_seg.get(src_id, 0) <= 1:
            continue
        sp_info = split_sources.get(src_id) or {}
        fx_b = angka(sp_info.get("fx_base"), 0.0)
        fy_b = angka(sp_info.get("fy_base"), 0.0)
        if ("freeze_x" in st_e and abs(angka(st_e.get("freeze_x")) - fx_b) > 0.05) or (
                "freeze_y" in st_e and abs(angka(st_e.get("freeze_y")) - fy_b) > 0.05):
            moved_split_ids.add(bid)

    main_typ = os.path.join(folder, "main.typ")
    body_path = os.path.join(folder, "body.typ")
    if not moved_split_ids or not os.path.isfile(main_typ) or not os.path.isfile(body_path):
        return None, None, None, moved_split_ids

    body_disk = baca_teks(body_path)
    peta_mid = None
    pdf_mid = None
    tmpd = tempfile.mkdtemp(dir=folder, prefix=".mid-split-")
    try:
        body_mid = render_body(body, dok, multi_seg_ids=multi_seg_ids)
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(body_mid)
        pos_mid, _ = _kueri_posisi_typst(main_typ)
        cand_pdf = os.path.join(tmpd, "mid.pdf")
        if pos_mid:
            rc, _, _ = _jalankan_typst([
                _program_typst(), "compile", "--root", BASE, "--font-path",
                os.path.join(BASE, "fonts"), main_typ, cand_pdf,
            ])
            if rc == 0 and os.path.isfile(cand_pdf):
                with open(os.path.join(tmpd, "body.typ"), "w", encoding="utf-8") as f:
                    f.write(body_mid)
                tulis_json(os.path.join(tmpd, "meta.json"), meta)
                dok_mid = json.loads(json.dumps(dok, ensure_ascii=False))
                dok_mid["body_hash"] = hash_teks(body_mid)
                tulis_json(os.path.join(tmpd, NAMA_DOKUMEN), dok_mid)
                res_mid = _simpan_peta_tata_letak(tmpd, pos_mid)
                peta_mid = (res_mid or {}).get("peta")
                if peta_mid:
                    pdf_mid = cand_pdf
    finally:
        try:
            with open(body_path, "w", encoding="utf-8") as f:
                f.write(body_disk)
        except OSError:
            pass
    if not peta_mid:
        shutil.rmtree(tmpd, ignore_errors=True)
        return None, None, None, moved_split_ids
    return peta_mid, pdf_mid, tmpd, moved_split_ids


def _aplikasi_edits_dan_splits_memori(folder, dok, body, meta, peta_lama, deletes, edits, splits,
                                       metadata_only=False, kunci_meta=None):
    """Terapkan deletes/edits/splits dengan penundaan edit freeze pada blok hasil split."""
    split_by_source = {}
    split_new_ids = set()
    for sp in splits:
        if not isinstance(sp, dict):
            continue
        sb = str(sp.get("block_id") or sp.get("id") or "")
        sn = str(sp.get("new_id") or "")
        if sb:
            split_by_source[sb] = sp
        if sn:
            split_new_ids.add(sn)
    split_source_ids = set(split_by_source.keys())

    deletes_awal = [d for d in deletes if str(d) not in split_new_ids]
    deletes_tunda = [d for d in deletes if str(d) in split_new_ids]
    hapus, lewat = _hapus_memori(dok, deletes_awal)
    dihapus = list(hapus)
    dilewati = list(lewat)
    jumlah_ubah = 1 if hapus else 0

    meta_penting = False
    meta_halaman1 = False
    deferred_edits = []
    deferred_freeze_sources = set()

    for e in edits:
        if not isinstance(e, dict):
            continue
        bid = str(e.get("block_id") or e.get("id") or "")
        blok = next((b for b in dok.get("blocks", []) if b.get("id") == bid), None)
        if not blok or bid in split_new_ids:
            deferred_edits.append(e)
            continue
        if bid in split_source_ids and isinstance(e.get("style"), dict) and any(
            k in e["style"] for k in _FREEZE_DEFER_KEYS
        ):
            st_e = e["style"]
            e_freeze = {"block_id": bid, "style": {k: st_e[k] for k in _FREEZE_DEFER_KEYS if k in st_e}}
            deferred_edits.append(e_freeze)
            deferred_freeze_sources.add(bid)
            e_pre = {k: v for k, v in e.items() if k != "style"}
            st_rest = {k: v for k, v in st_e.items() if k not in _FREEZE_DEFER_KEYS}
            if st_rest:
                e_pre["style"] = st_rest
            if not any(k in e_pre for k in ("text", "caption", "style", "table_cells")):
                continue
            e = e_pre
        if blok.get("jenis") == "meta":
            if blok.get("meta_key") in ("judul", "penulis"):
                meta_penting = True
            else:
                meta_halaman1 = True
        if _terapkan_edit_memori(blok, e):
            jumlah_ubah += 1
        if e.get("text") is not None:
            (blok.get("src") or {}).pop("seg_pieces", None)

    for sb, sp in split_by_source.items():
        if sp.get("frozen") and not sp.get("colbreak") and sb in deferred_freeze_sources:
            blok_sb = next((b for b in dok.get("blocks", []) if b.get("id") == sb), None)
            if blok_sb:
                st_sb = dict(blok_sb.get("style") or {})
                fx_sp = angka(sp.get("freeze_x"), st_sb.get("freeze_x", 0.0)) if sp.get("freeze_x") is not None else st_sb.get("freeze_x", 0.0)
                fy_sp = angka(sp.get("freeze_y"), st_sb.get("freeze_y", 0.0)) if sp.get("freeze_y") is not None else st_sb.get("freeze_y", 0.0)
                st_sb.update({"frozen": True, "unlocked_in_freeze": False, "freeze_x": fx_sp, "freeze_y": fy_sp})
                blok_sb["style"] = normal_gaya(blok_sb.get("tipe") or blok_sb.get("jenis", "paragraph"), st_sb)

    meta = terapkan_meta(dok, meta, kunci_meta or set())

    hitung_seg = {}
    for seg in (peta_lama.get("blocks") or []):
        sid = seg.get("id")
        if sid:
            hitung_seg[sid] = hitung_seg.get(sid, 0) + 1

    ada_split_beku = any(isinstance(sp, dict) and sp.get("frozen") and not sp.get("colbreak") for sp in splits)
    body_pre_split = None
    if ada_split_beku and not metadata_only and os.path.isfile(os.path.join(folder, "main.typ")):
        multi_seg_pre = {sid for sid, cnt in hitung_seg.items() if cnt > 1}
        body_pre_split = render_body(body, dok, multi_seg_ids=multi_seg_pre)

    hasil_split = []
    dipecah_ids = set()
    for sp in splits:
        if not isinstance(sp, dict):
            continue
        ok_sp, info_sp, galat_sp = _pecah_blok_memori(dok, sp, peta_lama=peta_lama, folder=folder)
        if not ok_sp:
            return {"ok": False, "galat": galat_sp or "Gagal memecah blok"}
        hasil_split.append(info_sp)
        if info_sp and info_sp.get("source_id"):
            dipecah_ids.add(info_sp["source_id"])
        jumlah_ubah += 1

    if deletes_tunda:
        h2, l2 = _hapus_memori(dok, deletes_tunda)
        dihapus.extend(h2)
        dilewati.extend(l2)
        if h2:
            jumlah_ubah += 1

    multi_seg_ids = {sid for sid, cnt in hitung_seg.items() if cnt > 1} - dipecah_ids

    if body_pre_split and hasil_split:
        _kalibrasi_pecah_beku(folder, body, dok, hasil_split, multi_seg_ids, body_pre_split)

    peta_mid, pdf_mid, tmpd_mid, moved_split_ids = _terapkan_prapindah_split_segmen(
        folder, body, dok, meta, multi_seg_ids, deferred_edits, hasil_split, hitung_seg
    ) if (not metadata_only and hasil_split and deferred_edits) else (None, None, None, set())

    try:
        for e in deferred_edits:
            bid = str(e.get("block_id") or e.get("id") or "")
            blok = next((b for b in dok.get("blocks", []) if b.get("id") == bid), None)
            if not blok:
                if bid not in deletes_tunda:
                    dilewati.append(bid)
                continue
            if _terapkan_edit_memori(blok, e):
                jumlah_ubah += 1
            if e.get("text") is not None:
                (blok.get("src") or {}).pop("seg_pieces", None)

        if not metadata_only:
            if peta_mid and moved_split_ids:
                _siapkan_seg_pieces_dok(folder, dok, peta_mid, kandidat_ids=moved_split_ids,
                                        hasil_pdf=pdf_mid)
            _siapkan_seg_pieces_dok(folder, dok, peta_lama)
    finally:
        if tmpd_mid:
            shutil.rmtree(tmpd_mid, ignore_errors=True)

    return {
        "ok": True,
        "meta": meta,
        "hasil_split": hasil_split,
        "dipecah_ids": dipecah_ids,
        "multi_seg_ids": multi_seg_ids,
        "jumlah_ubah": jumlah_ubah,
        "meta_penting": meta_penting,
        "meta_halaman1": meta_halaman1,
        "dihapus": dihapus,
        "dilewati": dilewati,
    }


def aksi_save(folder, data):
    """Terapkan seluruh draft lokal (pindah, hapus, edit, split) lalu kompilasi SEKALI."""
    folder = os.path.abspath(folder)
    moves = data.get("moves") if isinstance(data.get("moves"), list) else []
    deletes = data.get("deletes") if isinstance(data.get("deletes"), list) else []
    edits = data.get("edits") if isinstance(data.get("edits"), list) else []
    splits = data.get("splits") if isinstance(data.get("splits"), list) else []
    crops = data.get("crops") if isinstance(data.get("crops"), list) else []
    kaki_naik = data.get("kaki_naik") if isinstance(data.get("kaki_naik"), dict) else {}
    header_naik = data.get("header_naik") if isinstance(data.get("header_naik"), dict) else {}
    header_shift = data.get("header_shift")
    footer_shift = data.get("footer_shift")
    if len(moves) > 500 or len(deletes) > 1000 or len(edits) > 1000 or len(splits) > 500:
        return {"ok": False, "galat": "Draft terlalu besar untuk satu penyimpanan"}

    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    peta_lama = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}
    metadata_only = edit_metadata_saja(edits, moves, deletes, dok, crops=crops, kaki_naik=kaki_naik,
                                       header_naik=header_naik,
                                       header_shift=header_shift, footer_shift=footer_shift,
                                       splits=splits)
    kunci_meta = kunci_edit_metadata(edits, dok)
    meta_awal = json.dumps(meta, sort_keys=True, ensure_ascii=False)
    blok_awal = {b.get("id"): json.dumps(b, sort_keys=True, ensure_ascii=False)
                 for b in dok.get("blocks", [])}

    _simpan_snapshot(folder, dok, body, meta, "Sebelum simpan draft")

    dipindah, dihapus, dilewati = [], [], []
    jumlah_ubah = 0
    # 1) Operasi pindah berurutan (target masih ada saat operasi diterapkan).
    for mv in moves:
        if not isinstance(mv, dict):
            continue
        ok, body, ids, galat, berubah = _pindah_memori(
            dok, body, mv.get("block_ids") or mv.get("source_block_ids") or [],
            mv.get("target_id") or mv.get("target_block_id"),
            mv.get("position") or mv.get("posisi"), mv.get("placement"))
        if not ok:
            return {"ok": False, "galat": galat}
        if berubah:
            dipindah.extend(ids)
            jumlah_ubah += 1

    # 2-3) Hapus, edit, split & persiapan segmen beku (shared dengan aksi_preview).
    res_app = _aplikasi_edits_dan_splits_memori(
        folder, dok, body, meta, peta_lama, deletes, edits, splits,
        metadata_only=metadata_only, kunci_meta=kunci_meta,
    )
    if not res_app.get("ok"):
        return res_app
    meta = res_app["meta"]
    hasil_split = res_app["hasil_split"]
    multi_seg_ids = res_app["multi_seg_ids"]
    jumlah_ubah += res_app["jumlah_ubah"]
    meta_penting = res_app["meta_penting"]
    meta_halaman1 = res_app["meta_halaman1"]
    hapus = res_app["dihapus"]
    dihapus.extend(hapus)
    dilewati.extend(res_app["dilewati"])

    # 4) Terapkan crops gambar dan kaki-naik
    ubah_crop = _terapkan_crops_memori(dok, crops)
    jumlah_ubah += ubah_crop
    body, ubah_kaki = _terapkan_kaki_body(body, kaki_naik)
    if ubah_kaki:
        jumlah_ubah += 1
    body, ubah_kepala = _terapkan_kepala_body(body, header_naik)
    if ubah_kepala:
        jumlah_ubah += 1

    main_lama = baca_teks(os.path.join(folder, "main.typ"))
    main_baru, ubah_hf = _terapkan_header_footer_main(main_lama, header_shift=header_shift, footer_shift=footer_shift)
    if ubah_hf:
        jumlah_ubah += 1

    # Dokumen sudah berubah bila urutan, jumlah blok, atau isi blok/meta berubah.
    blok_akhir = {b.get("id"): json.dumps(b, sort_keys=True, ensure_ascii=False)
                  for b in dok.get("blocks", [])}
    if not jumlah_ubah and not ubah_kaki and not ubah_kepala and not ubah_hf and json.dumps(meta, sort_keys=True, ensure_ascii=False) == meta_awal \
            and blok_akhir == blok_awal:
        hasil = ruang_kerja(folder, paksa_kompilasi=False)
        hasil.update({"halaman_berubah": [], "disimpan": False, "diubah": 0,
                      "dipindah": [], "dihapus": [], "dilewati": dilewati, "splits": []})
        return hasil

    body_final = body if metadata_only else render_body(body, dok, multi_seg_ids=multi_seg_ids)
    for b in dok.get("blocks", []):
        src = b.get("src", {})
        src.pop("baru", None)
        src.pop("insert_after", None)
    k = _terapkan_dan_kompilasi(folder, dok, body_final, meta, main_text=(main_baru if ubah_hf else None))
    if not k.get("ok"):
        return k
    # Bersihkan penanda hapus setelah baris benar-benar hilang dari body.
    dok.pop("dihapus_pm", None)
    dok.pop("dihapus_objek", None)
    simpan_dokumen(folder, dok)
    dok = muat_dokumen(folder)
    label = "Simpan draft (%d perubahan)" % jumlah_ubah if jumlah_ubah else "Simpan draft"
    _simpan_snapshot(folder, dok, baca_teks(os.path.join(folder, "body.typ")),
                     baca_json(os.path.join(folder, "meta.json"), {}), label)
    hasil = ruang_kerja(folder, paksa_kompilasi=False)
    berubah = _halaman_berubah(peta_lama, baca_json(os.path.join(folder, "layout_map.json"), {}) or {})
    if meta_penting:
        berubah = list(range(1, int(hasil.get("halaman") or 0) + 1))
    elif meta_halaman1:
        berubah = sorted(set(berubah) | {1})
    hasil["halaman_berubah"] = berubah
    hasil["disimpan"] = True
    hasil["diubah"] = jumlah_ubah
    hasil["dipindah"] = list(dict.fromkeys(dipindah))
    hasil["dihapus"] = hapus
    hasil["dilewati"] = dilewati
    hasil["splits"] = [
        {"source_id": sp.get("source_id"), "new_id": sp.get("new_id"), "new_pm": sp.get("new_pm")}
        for sp in hasil_split if isinstance(sp, dict)
    ]
    return hasil


def aksi_preview(folder, data):
    """Kompilasi pratinjau draft TANPA menyentuh body/meta/dokumen asli.

    Dipakai auto-preview 600-800 ms setelah pengguna berhenti mengetik. Semua
    perubahan diterapkan ke salinan di ``_pratinjau_editor``; berkas artikel
    yang sesungguhnya baru ditulis oleh aksi ``save``.
    """
    folder = os.path.abspath(folder)
    moves = data.get("moves") if isinstance(data.get("moves"), list) else []
    deletes = data.get("deletes") if isinstance(data.get("deletes"), list) else []
    edits = data.get("edits") if isinstance(data.get("edits"), list) else []
    splits = data.get("splits") if isinstance(data.get("splits"), list) else []
    crops = data.get("crops") if isinstance(data.get("crops"), list) else []
    kaki_naik = data.get("kaki_naik") if isinstance(data.get("kaki_naik"), dict) else {}
    header_naik = data.get("header_naik") if isinstance(data.get("header_naik"), dict) else {}
    if len(moves) > 500 or len(deletes) > 1000 or len(edits) > 1000 or len(splits) > 500:
        return {"ok": False, "galat": "Draft terlalu besar untuk pratinjau"}

    dok = muat_dokumen(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    peta_lama = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}

    metadata_only = edit_metadata_saja(edits, moves, deletes, dok, crops=crops, kaki_naik=kaki_naik,
                                       header_naik=header_naik, splits=splits)
    kunci_meta = kunci_edit_metadata(edits, dok)
    for mv in moves:
        if not isinstance(mv, dict):
            continue
        ok, body, _ids, galat, _berubah = _pindah_memori(
            dok, body, mv.get("block_ids") or mv.get("source_block_ids") or [],
            mv.get("target_id") or mv.get("target_block_id"),
            mv.get("position") or mv.get("posisi"), mv.get("placement"))
        if not ok:
            return {"ok": False, "galat": galat}
    res_app = _aplikasi_edits_dan_splits_memori(
        folder, dok, body, meta, peta_lama, deletes, edits, splits,
        metadata_only=metadata_only, kunci_meta=kunci_meta,
    )
    if not res_app.get("ok"):
        return res_app
    meta = res_app["meta"]
    multi_seg_ids = res_app["multi_seg_ids"]
    _terapkan_crops_memori(dok, crops)
    body, _ = _terapkan_kaki_body(body, kaki_naik)
    body, _ = _terapkan_kepala_body(body, header_naik)

    body_final = body if metadata_only else render_body(body, dok, multi_seg_ids=multi_seg_ids)
    for b in dok.get("blocks", []):
        src = b.get("src", {})
        src.pop("baru", None)
        src.pop("insert_after", None)

    preview = os.path.join(folder, "_pratinjau_editor")
    os.makedirs(preview, exist_ok=True)
    kunci = open(os.path.join(preview, ".kunci"), "w")
    try:
        if fcntl:
            fcntl.flock(kunci, fcntl.LOCK_EX)
        with open(os.path.join(preview, "body.typ"), "w", encoding="utf-8") as f:
            f.write(body_final)
        tulis_json(os.path.join(preview, "meta.json"), meta)
        # main.typ pratinjau memakai template & config yang sama, hanya meta.json
        # dan body.typ yang diarahkan ke folder pratinjau.
        main = baca_teks(os.path.join(folder, "main.typ"))
        nama = os.path.basename(folder)
        pola_meta = 'json("/kerja/%s/meta.json")' % nama
        if pola_meta in main:
            main = main.replace(pola_meta, 'json("/kerja/%s/_pratinjau_editor/meta.json")' % nama)
        else:
            main = re.sub(r'json\("[^"]*/meta\.json"\)',
                          'json("/kerja/%s/_pratinjau_editor/meta.json")' % nama,
                          main, count=1)
        # main.typ lama dapat menyertakan body.typ dengan path absolut; arahkan
        # selalu ke body.typ relatif di folder pratinjau.
        main = re.sub(r'#include\s+"(?:[^"]*/)?body\.typ"', '#include "body.typ"', main)
        with open(os.path.join(preview, "main.typ"), "w", encoding="utf-8") as f:
            f.write(main)
        dok["body_hash"] = hash_teks(body_final)
        tulis_json(os.path.join(preview, NAMA_DOKUMEN), dok)
        k = kompilasi_svg(preview)
        if not k.get("ok"):
            return k
        hasil = ruang_kerja(preview, cache_saja=True, sertakan_qa=False)
        if not hasil.get("ok"):
            return hasil
        versi = int(time.time() * 1000)
        halaman = int(hasil.get("halaman") or 0)
        hasil.update({
            "artikel": nama,
            "preview": True,
            "versi": versi,
            "blocks": _blok_untuk_ui(dok),
            "riwayat": riwayat_info(folder),
            "terbitan": meta.get("terbitan") or {},
            "pages": [{"p": p, "url": "api.php?aksi=svg_preview&artikel=%s&p=%d&v=%d" %
                       (nama, p, versi), "lebar": hasil.get("lebar", LEBAR_HALAMAN),
                       "tinggi": hasil.get("tinggi", TINGGI_HALAMAN)}
                      for p in range(1, halaman + 1)],
        })
        hasil.update(_info_doi(folder, meta, halaman))
        return hasil
    finally:
        if fcntl:
            try:
                fcntl.flock(kunci, fcntl.LOCK_UN)
            except OSError:
                pass
        kunci.close()


def aksi_undo(folder):
    folder = os.path.abspath(folder)
    idx = _baca_indeks(folder)
    if idx["pos"] <= 0:
        return {"ok": False, "galat": "Tidak ada langkah untuk dibatalkan"}
    idx["pos"] -= 1
    item = idx["items"][idx["pos"]]
    dok, body, meta = _muat_snapshot(folder, item)
    k = _terapkan_dan_kompilasi(folder, dok, body, meta)
    if not k.get("ok"):
        idx["pos"] += 1
        return k
    _tulis_indeks(folder, idx)
    simpan_dokumen(folder, dok)
    return ruang_kerja(folder, paksa_kompilasi=False)


def aksi_redo(folder):
    folder = os.path.abspath(folder)
    idx = _baca_indeks(folder)
    if idx["pos"] >= len(idx["items"]) - 1:
        return {"ok": False, "galat": "Tidak ada langkah untuk diulangi"}
    idx["pos"] += 1
    item = idx["items"][idx["pos"]]
    dok, body, meta = _muat_snapshot(folder, item)
    k = _terapkan_dan_kompilasi(folder, dok, body, meta)
    if not k.get("ok"):
        idx["pos"] -= 1
        return k
    _tulis_indeks(folder, idx)
    simpan_dokumen(folder, dok)
    return ruang_kerja(folder, paksa_kompilasi=False)


def aksi_page(folder, data):
    folder = os.path.abspath(folder)
    body = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    hal = int(data.get("hal", 0) or 0)
    body2 = body
    if hal > 0 and data.get("baris") is not None:
        body2, _ = _terapkan_kaki_body(body2, {hal: data["baris"]})
    if hal > 0 and data.get("header_baris") is not None:
        body2, _ = _terapkan_kepala_body(body2, {hal: data["header_baris"]})
    header_shift = data.get("header_shift")
    footer_shift = data.get("footer_shift")
    main_lama = baca_teks(os.path.join(folder, "main.typ"))
    main_baru, ubah_hf = _terapkan_header_footer_main(main_lama, header_shift=header_shift, footer_shift=footer_shift)
    if body2 == body and not ubah_hf:
        return ruang_kerja(folder)
    dok = muat_dokumen(folder)
    k = _terapkan_dan_kompilasi(folder, dok, body2, meta, main_text=(main_baru if ubah_hf else None))
    if not k.get("ok"):
        return k
    label = "Header/Footer hlm %d" % hal if ubah_hf else "Footer hlm %d" % hal
    _simpan_snapshot(folder, dok, baca_teks(os.path.join(folder, "body.typ")),
                     baca_json(os.path.join(folder, "meta.json"), {}), label)
    return ruang_kerja(folder)


# ======================================================================
#  Auto-fix kolom
# ======================================================================
def _ukur_kolom_cepat(folder):
    """Kompilasi PDF sementara lalu ukur kekurangan kolom tanpa QA penuh."""
    sys.path.insert(0, BASE)
    import build  # noqa: E402
    pdf = os.path.join(folder, "_editor_ukur.pdf")
    rc, out, err = build.jalankan([build.TYPST, "compile", "--root", BASE, "--font-path",
                                   os.path.join(BASE, "fonts"), os.path.join(folder, "main.typ"), pdf])
    if rc != 0:
        return None, err[-1500:]
    pos, galat = build.kueri_posisi(os.path.join(folder, "main.typ"))
    if pos is None:
        return None, galat
    body = baca_teks(os.path.join(folder, "body.typ"))
    kolom = build.ukur_kolom(pdf, pos, body)
    try:
        os.remove(pdf)
    except OSError:
        pass
    return kolom, ""


def aksi_auto_fix(folder, data):
    folder = os.path.abspath(folder)
    hal = int(data.get("hal", 0) or 0)
    dok = muat_dokumen(folder)
    body_awal = baca_teks(os.path.join(folder, "body.typ"))
    meta = baca_json(os.path.join(folder, "meta.json"), {}) or {}
    peta = baca_json(os.path.join(folder, "layout_map.json"), {}) or {}

    kolom0, galat = _ukur_kolom_cepat(folder)
    if kolom0 is None:
        return {"ok": False, "galat": "Pengukuran kolom gagal: " + galat}

    d_kiri0 = next((float(x["kurang"]) for x in kolom0 if x["hal"] == hal and x["kol"] == 0), 0.0)
    d_kanan0 = next((float(x["kurang"]) for x in kolom0 if x["hal"] == hal and x["kol"] == 1), 0.0)

    # 1. Cek apakah ada pelanggaran: kanan < kiri - 1.5 atau kiri > 2.0 atau kanan > 2.0
    if not (d_kanan0 < d_kiri0 - 1.5 or d_kiri0 > 2.0 or d_kanan0 > 2.0):
        return {"ok": True, "pesan": "Kolom sudah seimbang (kiri: %.1f pt, kanan: %.1f pt)" % (d_kiri0, d_kanan0),
                "workspace": ruang_kerja(folder)}

    total_hal = int(peta.get("halaman") or max((x["hal"] for x in kolom0), default=hal))

    def _skor(kiri, kanan):
        pelanggaran = 1 if kanan < kiri - 1.5 else 0
        return (pelanggaran, round(max(0.0, kiri), 1), round(max(0.0, kanan), 1))

    skor_awal = _skor(d_kiri0, d_kanan0)
    terbaik = None  # (skor, body_baru, dok_baru, pesan)

    # Langkah 1 (Footer #kaki-naik simetris 11pt / 22pt)
    # Berlaku jika bukan halaman terakhir, selisih kiri-kanan <= 2.2 pt, dan kanan mendekati 11pt atau 22pt
    if hal < total_hal and abs(d_kiri0 - d_kanan0) <= 2.2:
        baris_kaki = 0
        if abs(d_kanan0 - 11.0) <= 2.2:
            baris_kaki = 1
        elif abs(d_kanan0 - 22.0) <= 2.2:
            baris_kaki = 2
        if baris_kaki > 0:
            body_kaki, ubah_k = _terapkan_kaki_body(body_awal, {str(hal): baris_kaki})
            if ubah_k:
                with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
                    f.write(body_kaki)
                kolom_k, _ = _ukur_kolom_cepat(folder)
                if kolom_k is not None:
                    dk_k = next((float(x["kurang"]) for x in kolom_k if x["hal"] == hal and x["kol"] == 0), 0.0)
                    dkn_k = next((float(x["kurang"]) for x in kolom_k if x["hal"] == hal and x["kol"] == 1), 0.0)
                    sk = _skor(dk_k, dkn_k)
                    if sk < skor_awal:
                        terbaik = (sk, body_kaki, dok,
                                   "Footer halaman %d dinaikkan %d baris (%.0f pt); kolom (%.1f, %.1f) -> (%.1f, %.1f)" %
                                   (hal, baris_kaki, baris_kaki * 11.0, d_kiri0, d_kanan0, dk_k, dkn_k))

    # Pulihkan body sebelum lanjut ke langkah 2 bila diuji
    with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
        f.write(body_awal)

    # Langkah 2 (Iterasi ukuran gambar width_pct pada halaman <= hal)
    id_hal = {s.get("id") for s in peta.get("blocks", []) if int(s.get("page") or 0) <= hal}
    kandidat_figur = [b for b in dok.get("blocks", [])
                      if b.get("jenis") == "figure" and (b.get("id") in id_hal or not id_hal)]

    if kandidat_figur:
        deltas = (-5.0, 5.0, -10.0, 10.0, -15.0, 15.0, -20.0, 20.0, -25.0, -30.0)
        skor_saat_ini = terbaik[0] if terbaik is not None else skor_awal

        for fig in kandidat_figur:
            st_asli = dict(fig.get("style") or {})
            pct_asli = angka(st_asli.get("width_pct"), 100.0)
            for delta in deltas:
                pct_baru = clamp(pct_asli + delta, 70.0, 120.0)
                if abs(pct_baru - pct_asli) < 0.1:
                    continue
                st_coba = dict(st_asli)
                st_coba["width_pct"] = pct_baru
                fig["style"] = normal_gaya("figure", st_coba)
                body_coba = render_body(body_awal, dok)
                with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
                    f.write(body_coba)
                kolom_c, _ = _ukur_kolom_cepat(folder)
                if kolom_c is not None:
                    dk_c = next((float(x["kurang"]) for x in kolom_c if x["hal"] == hal and x["kol"] == 0), 0.0)
                    dkn_c = next((float(x["kurang"]) for x in kolom_c if x["hal"] == hal and x["kol"] == 1), 0.0)
                    sk_c = _skor(dk_c, dkn_c)
                    if sk_c < skor_saat_ini:
                        skor_saat_ini = sk_c
                        dok_salinan = json.loads(json.dumps(dok))
                        terbaik = (sk_c, body_coba, dok_salinan,
                                   "Skala gambar '%s' diubah ke %.0f%% (delta %+.0f%%); kolom (%.1f, %.1f) -> (%.1f, %.1f)" %
                                   (fig.get("id", "figure"), pct_baru, delta, d_kiri0, d_kanan0, dk_c, dkn_c))
            fig["style"] = st_asli

    # Jika tidak ada perbaikan
    if terbaik is None or terbaik[0] >= skor_awal:
        with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
            f.write(body_awal)
        return {"ok": False,
                "galat": "Penyesuaian gambar/footer belum menyeimbangkan kolom (kiri: %.1f pt, kanan: %.1f pt)" %
                (d_kiri0, d_kanan0)}

    sk_pilih, body_pilih, dok_pilih, pesan_pilih = terbaik
    k = _terapkan_dan_kompilasi(folder, dok_pilih, body_pilih, meta)
    if not k.get("ok"):
        with open(os.path.join(folder, "body.typ"), "w", encoding="utf-8") as f:
            f.write(body_awal)
        return k

    simpan_dokumen(folder, dok_pilih)
    _simpan_snapshot(folder, dok_pilih, body_pilih, meta, "Auto-fix hlm %d" % hal)
    ws = ruang_kerja(folder)
    return {"ok": True, "pesan": pesan_pilih, "workspace": ws}


# ======================================================================
#  CLI
# ======================================================================
def _baca_input(path):
    if not path:
        return {}
    if path == "-":
        return json.load(sys.stdin)
    return baca_json(path, {}) or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("aksi", choices=["get", "workspace", "qa", "update", "insert", "delete", "batch",
                                     "move", "pindah", "save", "preview", "undo", "redo",
                                     "auto_fix", "page", "migrate"])
    ap.add_argument("folder")
    ap.add_argument("--json", help="berkas JSON masukan")
    a = ap.parse_args()
    folder = os.path.abspath(a.folder)
    if not os.path.isdir(folder):
        print(json.dumps({"ok": False, "galat": "Folder artikel tidak ada"}, ensure_ascii=False))
        return
    data = _baca_input(a.json) if a.aksi in ("qa", "update", "insert", "delete", "batch",
                                             "move", "pindah", "save", "preview", "auto_fix", "page") else {}
    try:
        if a.aksi == "get":
            hasil = ruang_kerja(folder, sertakan_qa=True)
        elif a.aksi == "workspace":
            # get_workspace: hanya cache SVG/layout_map, tanpa kompilasi ulang & tanpa QA.
            hasil = ruang_kerja(folder, cache_saja=True, sertakan_qa=False)
        elif a.aksi == "qa":
            hasil = aksi_qa(folder, data)
        elif a.aksi == "migrate":
            dok, body, meta = migrasi(folder)
            tulis_json(os.path.join(folder, NAMA_DOKUMEN), dok)
            _pastikan_snapshot(folder, dok, body, meta, "Migrasi blok awal", paksa=True)
            hasil = ruang_kerja(folder)
        elif a.aksi == "update":
            hasil = aksi_update(folder, data)
        elif a.aksi == "insert":
            hasil = aksi_insert(folder, data)
        elif a.aksi == "delete":
            hasil = aksi_delete(folder, data)
        elif a.aksi == "batch":
            hasil = aksi_batch(folder, data)
        elif a.aksi in ("move", "pindah"):
            hasil = aksi_move(folder, data)
        elif a.aksi == "save":
            hasil = aksi_save(folder, data)
        elif a.aksi == "preview":
            hasil = aksi_preview(folder, data)
        elif a.aksi == "undo":
            hasil = aksi_undo(folder)
        elif a.aksi == "redo":
            hasil = aksi_redo(folder)
        elif a.aksi == "auto_fix":
            hasil = aksi_auto_fix(folder, data)
        elif a.aksi == "page":
            hasil = aksi_page(folder, data)
        else:
            hasil = {"ok": False, "galat": "Aksi tidak dikenal"}
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        hasil = {"ok": False, "galat": "Kesalahan mesin: %s" % e}
    print(json.dumps(hasil, ensure_ascii=False))


if __name__ == "__main__":
    main()
