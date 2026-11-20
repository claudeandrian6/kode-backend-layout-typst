#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — naskah .docx  →  PDF siap terbit (pandoc + Typst), tanpa Word/Scribus.

Pemakaian:
    python3 build.py naskah.docx --jurnal bsscd --volume 5 --nomor 3 --tahun 2026 \
        --halaman-awal 253 [--meta meta_ojs.json] [--keluar hasil/]

Alur:
  1. pandoc  : docx → AST JSON (+ ekstrak gambar)
  2. build.py: pisahkan metadata halaman 1, klasifikasikan isi (judul bagian,
               sub-bagian, gambar+keterangan, tabel+keterangan, daftar,
               kutipan wawancara, referensi) → AST baru berisi panggilan template
  3. pandoc  : AST → body.typ
  4. typst   : main.typ (template_jurnal.typ + config jurnal + meta) → PDF
  5. QA      : gerbang otomatis → qa.json, exit 0 = LULUS, 2 = DITAHAN

Hanya pustaka standar Python (3.8+). QA teks memakai PyMuPDF bila terpasang.
"""
import argparse
import gc     # FIX-MEMORI-GC: lepas memori PyMuPDF saat cache teks dibersihkan
import glob   # FIX-QA-TATA-LETAK
import json
import math   # FIX-KEPUTUSAN-LUAR
import os
import re
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
PANDOC = os.environ.get("PANDOC_BIN", "pandoc")
TYPST = os.environ.get("TYPST_BIN", "typst")
def cari_soffice():
    """FIX-SOFFICE-CARI: LibreOffice dari SOFFICE_BIN, PATH, atau lokasi instalasi umum
    (Windows tidak menambahkan folder LibreOffice ke PATH)."""
    import shutil
    if os.environ.get("SOFFICE_BIN"):
        return os.environ["SOFFICE_BIN"]
    for nama in ("soffice", "libreoffice", "soffice.exe"):
        p = shutil.which(nama)
        if p:
            return p
    calon = [os.path.join(os.environ.get(v, ""), "LibreOffice", "program", "soffice.exe")
             for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    calon += [r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              "/usr/bin/soffice", "/usr/local/bin/soffice", "/opt/libreoffice/program/soffice"]
    for p in calon:
        if p and os.path.isfile(p):
            return p
    return "soffice"


SOFFICE = cari_soffice()   # FIX-CHART


def cari_php():
    """FIX-NORMALISASI: PHP CLI dari PHP_BIN, PATH, atau lokasi instalasi umum (XAMPP, Laragon,
    WAMP, CloudLinux/Hostinger)."""
    import glob
    import shutil
    if os.environ.get("PHP_BIN"):
        return os.environ["PHP_BIN"]
    for nama in ("php", "php.exe"):
        p = shutil.which(nama)
        if p:
            return p
    calon = [r"C:\xampp\php\php.exe", r"C:\php\php.exe", "/usr/bin/php", "/usr/local/bin/php",
             "/opt/homebrew/bin/php"]
    for pola in (r"C:\laragon\bin\php\*\php.exe", r"C:\wamp64\bin\php\*\php.exe",
                 "/opt/alt/php*/usr/bin/php", "/opt/cpanel/ea-php*/root/usr/bin/php"):
        calon += sorted(glob.glob(pola), reverse=True)
    for p in calon:
        if os.path.isfile(p):
            return p
    return None


def normalisasi_naskah(docx, workdir, laporan):
    """FIX-NORMALISASI: rapikan struktur naskah Word dengan normalisasi.php (judul/paragraf/entri
    referensi yang digabung Shift+Enter, judul sebaris, track changes, komentar, kontrol konten, ...)
    sebelum dibaca pandoc. Bila PHP tidak tersedia, naskah asli dipakai apa adanya."""
    skrip = os.path.join(BASE, "normalisasi.php")
    php = cari_php()
    if not os.path.isfile(skrip) or not php:
        pesan = "Normalisasi naskah dilewati: " + (
            "normalisasi.php tidak ada di samping build.py" if not os.path.isfile(skrip)
            else "PHP tidak ditemukan (pasang PHP atau isi PHP_BIN)")
        laporan["peringatan"].append(pesan)
        cetak("   !! " + pesan)
        return docx
    hasil = os.path.join(workdir, "_normal.docx")
    lap_path = os.path.join(workdir, "normalisasi.json")
    rc, out, err = jalankan([php, skrip, docx, hasil, "--laporan", lap_path, "--diam"], cwd=workdir)
    try:
        with open(lap_path, encoding="utf-8") as f:
            lap = json.load(f)
    except (OSError, ValueError):
        lap = {"ok": False, "galat": (err or out or "tanpa keterangan").strip()[:300]}
    if rc != 0 or not lap.get("ok") or not os.path.isfile(hasil):
        pesan = "Normalisasi naskah gagal, naskah asli dipakai: " + str(lap.get("galat", "") or "tanpa keterangan")
        laporan["peringatan"].append(pesan)
        cetak("   !! " + pesan)
        return docx
    laporan["normalisasi"] = lap.get("perubahan", [])
    cetak("   normalisasi: " + ("{} perubahan".format(len(lap["perubahan"])) if lap.get("perubahan")
                              else "naskah sudah rapi, tidak ada yang diubah"))
    for p in lap.get("perubahan", []):
        cetak("   normalisasi: " + p["jenis"] + (": " + p["teks"] if p.get("teks") else ""))
    for p in lap.get("peringatan", []):
        laporan["peringatan"].append("Naskah: " + p)
        cetak("   ! " + p)
    # FIX-PROFIL-IDEAL: rapor kesesuaian naskah dengan profil naskah ideal, per bagian
    laporan["rapor_naskah"] = lap.get("rapor", [])
    if lap.get("rapor"):
        cetak("   Rapor kesesuaian dengan profil naskah ideal:")
        for r in lap["rapor"]:
            cetak("     [{}] {}{}".format(r["status"].upper(), r["bagian"],
                                         (": " + r["catatan"]) if r.get("catatan") else ""))
    return hasil
EDISI = {1: "March", 2: "June", 3: "September", 4: "December"}

LEBAR_KOLOM_PT = 213.6
LEBAR_TEKS_PT = 442.2           # = LEBAR-TEKS di template_jurnal.typ
BARIS_TABEL_PANJANG = 28        # > ini → tabel di halaman 1-kolom tersendiri
# FIX-POTONG-KHUSUS: target potong kolom manual per naskah (kalibrasi tata letak).
# Isi: (halaman cetak/footer, kolom 0=kiri 1=kanan, jumlah baris yang dipindah).
# Dikosongkan untuk naskah lain; diisi di main() hanya untuk naskah yang ditargetkan.
POTONG_KHUSUS = []
HALAMAN_AWAL_CETAK = [None]
LEBAR_TEKS_WORD = [468.0]       # FIX-LEBAR-TABEL: diisi dari sectPr docx di main()
CHART_LEBAR = {}                # FIX-UKURAN-CHART: nama png chart -> lebar tampil (pt)
CHART_EFEKTIF = {}              # FIX-QA-CHART: nama png chart -> ukuran teks efektif (pt)
CHART_EFEKTIF_SKALA = set()     # FIX-QA-CHART: chart yang skala objeknya sudah diterapkan
UKURAN_NARASI_PT = 11.0


def edisi_jurnal(cfg_path, nomor):
    """FIX-EDISI-CFG: bulan terbitan dari config jurnal; default EDISI (Maret/Juni/Sept/Des)."""
    with open(cfg_path, encoding="utf-8") as f:
        peta = json.load(f).get("edisi") or EDISI
    peta = {int(k): v for k, v in peta.items()}
    if nomor not in peta:
        sys.exit("Nomor {} tidak berlaku untuk jurnal ini (pilihan: {})".format(
            nomor, ", ".join("{}={}".format(k, v) for k, v in sorted(peta.items()))))
    return peta[nomor]


def lebar_teks_word(docx):
    """FIX-LEBAR-TABEL: lebar area teks naskah Word (pt) = pgSz - pgMar kiri - kanan."""
    import zipfile
    try:
        x = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
        sect = re.findall(r"<w:sectPr[ >].*?</w:sectPr>", x, flags=re.S)[-1]
        w = int(re.search(r'<w:pgSz [^>]*w:w="(\d+)"', sect).group(1))
        kiri = int(re.search(r'<w:pgMar [^>]*w:left="(\d+)"', sect).group(1))
        kanan = int(re.search(r'<w:pgMar [^>]*w:right="(\d+)"', sect).group(1))
        return (w - kiri - kanan) / 20.0
    except Exception:
        return 468.0

# ======================================================================
#  Utilitas AST pandoc
# ======================================================================
def stringify(x):
    if isinstance(x, list):
        return "".join(stringify(i) for i in x)
    if not isinstance(x, dict):
        return ""
    t = x.get("t")
    c = x.get("c")
    if t == "Str":
        return c
    if t in ("Space", "SoftBreak", "LineBreak"):
        return " "
    if t == "Math":
        return c[1]
    if t in ("Code", "RawInline"):
        return c[1]
    if t in ("Image", "Link"):
        return stringify(c[1])
    if t == "Quoted":
        return '"' + stringify(c[1]) + '"'
    if t in ("Cite", "Span"):
        return stringify(c[1])
    if t in ("Note",):
        return ""
    if isinstance(c, list):
        return stringify(c)
    return ""


def raw_b(s):
    return {"t": "RawBlock", "c": ["typst", s]}


def raw_i(s):
    return {"t": "RawInline", "c": ["typst", s]}


def normalkan_tabel_html(ast):
    """Baca tabel HTML sebagai AST Table; jangan serahkan HTML ke writer Typst."""
    def jalan(x):
        if isinstance(x, list):
            hasil = []
            for v in x:
                if isinstance(v, dict) and v.get("t") in ("RawBlock", "CodeBlock"):
                    c = v["c"]
                    if re.match(r"\s*<table\b", c[1], re.I):
                        rc, out, err = jalankan([PANDOC, "-f", "html", "-t", "json"],
                                                input=c[1].encode("utf-8"))
                        if rc:
                            raise ValueError("Tabel HTML tidak dapat dibaca: " + err)
                        bs = json.loads(out)["blocks"]
                        if not any(b.get("t") == "Table" for b in bs):
                            raise ValueError("Tabel HTML tidak menghasilkan AST Table")
                        hasil.extend(jalan(bs))
                        continue
                hasil.append(jalan(v))
            return hasil
        if isinstance(x, dict):
            return {k: jalan(v) for k, v in x.items()}
        return x
    return jalan(ast)


def tabel_ast_typst(ast, workdir):
    """Writer native: Pandoc Typst versi lama meratakan rowspan/colspan menjadi sel kosong."""
    cache = {}
    def jalan(x):
        if isinstance(x, list):
            return [jalan(v) for v in x]
        if not isinstance(x, dict):
            return x
        if x.get("t") != "Table":
            return {k: jalan(v) for k, v in x.items()}
        c = x["c"]
        rows = list(c[3][1])
        for body in c[4]:
            rows.extend(body[2])
            rows.extend(body[3])
        rows.extend(c[5][1])
        occupied, cells = set(), []
        head_rows = len(c[3][1]) or min(1, len(rows))
        if any(y + cell[2] > head_rows for y, row in enumerate(rows[:head_rows]) for cell in row[1]):
            head_rows = 0  # rowspan melintasi batas kepala tidak boleh dipotong oleh table.header
        for y, row in enumerate(rows):
            if y == 0 and head_rows:
                cells.append("  table.header(")
            if y == head_rows and head_rows:
                cells.append("  ),")
            col = 0
            for cell in row[1]:
                while (col, y) in occupied:
                    col += 1
                rs, cs = cell[2:4]
                if rs < 1 or cs < 1 or col + cs > len(c[2]):
                    raise ValueError("Span sel tabel Pandoc tidak sah")
                key = json.dumps(cell[4], sort_keys=True)
                if key not in cache:
                    doc = {"pandoc-api-version": ast["pandoc-api-version"], "meta": {},
                           "blocks": jalan(cell[4])}
                    rc, out, err = jalankan([PANDOC, "-f", "json", "-t", "typst", "--wrap=none"],
                                           input=json.dumps(doc).encode("utf-8"), cwd=workdir)
                    if rc:
                        raise ValueError("Isi sel tabel gagal dikonversi: " + err)
                    cache[key] = out.strip()
                cells.append("  table.cell(x: {}, y: {}, rowspan: {}, colspan: {})[{}],".format(
                    col, y, rs, cs, cache[key]))
                occupied.update((xx, yy) for xx in range(col, col + cs) for yy in range(y, y + rs))
                col += cs
        if head_rows and head_rows == len(rows):
            cells.append("  ),")
        return raw_b("#figure(align(center)[#table(\n  columns: {},\n{}\n)], kind: table)".format(
            len(c[2]), "\n".join(cells)))
    return jalan(ast)


def plain(inl):
    return {"t": "Plain", "c": inl}


def inlines_of(block):
    if block.get("t") in ("Para", "Plain"):
        return block["c"]
    return None


def buka_blok_pandoc(blocks):
    """Buka kutipan indentasi di bagian depan dan kutipan yang memuat gambar.

    Pandoc dapat menggabungkan beberapa paragraf Word berindentasi menjadi satu
    BlockQuote, termasuk penulis/afiliasi atau gambar dan keterangannya. Kutipan
    biasa di badan naskah tetap dipertahankan sebagai kutipan.
    """
    hasil = []
    depan = True
    referensi = False
    for b in blocks:
        t = b.get("t")
        inl = inlines_of(b) or (b["c"][2] if t == "Header" else [])
        teks = stringify(inl).strip()
        if t == "Header" and teks and is_header(teks, ketat=True):
            depan = False
            referensi = bersih_nomor(teks).upper() in ("REFERENCES", "REFERENCE", "DAFTAR PUSTAKA", "RUJUKAN")
        narasi = t == "BlockQuote" and all(
            v.get("t") in ("Para", "Plain") and len(stringify(v)) > 220
            and not re.match(r'^[\s]*[“‘\"\']', stringify(v))
            and not only_wrapped(inlines_of(v) or [], "Emph") for v in b["c"])
        if t == "BlockQuote" and (depan or referensi or narasi or any(
                '"t": "Image"' in json.dumps(x) for x in b["c"])):
            hasil.extend(b["c"])
        else:
            hasil.append(b)
        if re.match(r"^(?:key[\s\-]*words?|kata[\s\-]*(?:kata[\s\-]*)?kunci)\b", teks, re.I):
            depan = False
    return hasil


def only_wrapped(inl, tag):
    """True bila seluruh isi (selain spasi) terbungkus satu jenis format (Strong/Emph)."""
    isi = [i for i in inl if i.get("t") not in ("Space", "SoftBreak")]
    return bool(isi) and all(i.get("t") == tag for i in isi)


def unwrap(inl, tag):
    out = []
    for i in inl:
        if i.get("t") == tag:
            out.extend(i["c"])
        else:
            out.append(i)
    return out


def drop_prefix(inl, n):
    """Buang n karakter pertama (versi stringify) dari daftar inline."""
    out = list(inl)
    while out and n > 0:
        first = out[0]
        s = stringify(first)
        if len(s) <= n:
            n -= len(s)
            out.pop(0)
        elif first.get("t") == "Str":
            out[0] = {"t": "Str", "c": first["c"][n:]}
            n = 0
        elif first.get("t") in ("Strong", "Emph"):
            sisa = drop_prefix(first["c"], n)
            out[0] = {"t": first["t"], "c": sisa}
            n = 0
        else:
            break
    while out and out[0].get("t") in ("Space", "SoftBreak"):
        out.pop(0)
    return out


# FIX-SUB-NOMOR: nomor sub-bab ("2.1", "2.1.", "3.2.1") dibuang sesuai gaya jurnal
RE_NOMOR_SUB = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+(?=\S)")


def buang_nomor_sub(inl):
    m = RE_NOMOR_SUB.match(stringify(inl))
    return drop_prefix(inl, len(m.group(0))) if m else inl


def ke_typst_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ukuran_ke_pt(v):
    if not v:
        return 0.0
    m = re.match(r"([\d.]+)\s*(in|cm|mm|pt|px|%)?", v)
    if not m:
        return 0.0
    n = float(m.group(1))
    u = m.group(2) or "px"
    return {"in": 72.0, "cm": 28.3465, "mm": 2.83465, "pt": 1.0, "px": 0.75}.get(u, 0.0) * n


# ======================================================================
#  Aturan klasifikasi (dipindahkan dari word_to_json.py / json_to_scribus.py)
# ======================================================================
KATA_BAGIAN = [
    "INTRODUCTION", "PENDAHULUAN", "METHOD", "METHODS", "METODE",
    "RESEARCH METHOD", "RESEARCH METHODS", "RESULT", "RESULTS", "HASIL", "PEMBAHASAN",
    "RESULT AND DISCUSSION", "RESULTS AND DISCUSSION", "HASIL DAN PEMBAHASAN",
    "DISCUSSION", "CONCLUSION", "CONCLUSIONS", "KESIMPULAN", "REFERENCES", "REFERENCE",
    "DAFTAR PUSTAKA", "DECLARATION", "ACKNOWLEDGEMENT", "ACKNOWLEDGEMENTS",
    "AUTHOR CONTRIBUTIONS",
    # FIX-JUDUL-DEKLARASI: judul lengkap dari MAIN_HEADERS json_to_scribus.py (cocok persis)
    "DECLARATION OF GENERATIVE AI USAGE IN THE WRITING PROCESS",
    "AUTHORS' DECLARATION OF AI USE",
    # FIX-BAGIAN-LUAS: lampiran sesudah REFERENCES, variasi lain, bahasa Melayu (sejalan normalisasi.php)
    "APPENDIX", "APPENDICES", "LAMPIRAN", "ATTACHMENT", "ATTACHMENTS", "SUPPLEMENTARY MATERIAL",
    # (Literature Review, Tinjauan Pustaka, Landasan Teori, Findings, dsb. TIDAK dimasukkan: sering
    # menjadi sub-bab; bila ditulis KAPITAL tetap dikenali sebagai bagian lewat aturan baris kapital)
    "METODOLOGI", "METHODOLOGY", "RESEARCH METHODOLOGY", "METODE PENELITIAN", "MATERIALS AND METHODS",
    "BAHAN DAN METODE", "RESEARCH METHODS AND MATERIALS",
    "METHODS AND MATERIALS", "RESULTS AND ANALYSIS", "ANALYSIS AND DISCUSSION", "RESULTS AND DISCUSSIONS",
    "FINDINGS AND DISCUSSION", "SIMPULAN", "PENUTUP", "CONCLUSION AND RECOMMENDATION",
    "CONCLUSIONS AND RECOMMENDATIONS", "KESIMPULAN DAN SARAN", "BIBLIOGRAPHY", "DAFTAR RUJUKAN",
    "ACKNOWLEDGMENT", "ACKNOWLEDGMENTS",
    "PENGENALAN", "KAEDAH", "KAEDAH KAJIAN", "METODOLOGI KAJIAN", "DAPATAN", "DAPATAN KAJIAN", "PERBINCANGAN",
    "DAPATAN DAN PERBINCANGAN", "RUJUKAN", "PENGHARGAAN",
]
BAGIAN_REFERENSI = {"REFERENCES", "REFERENCE", "DAFTAR PUSTAKA", "BIBLIOGRAPHY", "DAFTAR RUJUKAN", "RUJUKAN"}
BAGIAN_WAJIB = [
    ("INTRODUCTION", "PENDAHULUAN"),
    ("METHOD", "METHODS", "METODE", "RESEARCH METHOD", "RESEARCH METHODS"),
    ("RESULT", "RESULTS", "HASIL", "RESULT AND DISCUSSION", "RESULTS AND DISCUSSION",
     "HASIL DAN PEMBAHASAN", "DISCUSSION"),
    ("CONCLUSION", "CONCLUSIONS", "KESIMPULAN"),
    ("REFERENCES", "REFERENCE", "DAFTAR PUSTAKA"),
]
SAMPAH = {"top of form", "bottom of form", "skip to main content", "print this page",
          "atas formulir", "bawah formulir"}


def bersih_nomor(t):
    # FIX-NOMOR: hanya buang penomoran ("1.", "2.1", "IV.", "-", "•"), bukan huruf I/V/X awal kata
    # FIX-JUDUL-BULLET: judul Word dapat diawali bullet (▪, ●, ○, ...) + NBSP; samakan dengan
    # n_tanpa_nomor() di normalisasi.php agar INTRODUCTION dikenali sebagai bagian wajib.
    return re.sub(r"^\s*(?:(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.)\s+|[\-•▪●○◦‣·■□➢]\s*)", "", t).strip()


def teks_judul(inl):
    """FIX-JUDUL-BERSIH: teks judul tanpa catatan kaki/komentar dan karakter tak terlihat
    (spasi tak-putus, zero-width, BOM) yang membuat judul tidak dikenali."""
    bersih = [x for x in (inl or []) if x.get("t") != "Note"]
    t = stringify(bersih).replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", t).strip()


def is_header(teks, ketat=False):
    stripped = bersih_nomor(teks)
    clean = re.sub(r"\s+", " ", stripped.upper().replace("\xa0", " ")).strip()
    if not any(c.isalpha() for c in clean):
        return False
    semua_kapital = stripped == stripped.upper()
    # FIX-JUDUL-BERSIH: nama bagian baku dicocokkan tanpa batas panjang
    for k in KATA_BAGIAN:
        if clean == k:
            return True
        # FIX-ACKNOWLEDGMENT: toleransi jika teks memiliki karakter tersembunyi/spasi
        if k in ("ACKNOWLEDGMENT", "ACKNOWLEDGEMENT") and clean.startswith(k):
            return True
        if (clean.startswith(k + " ") or clean.startswith(k + ":")) and semua_kapital:
            return True
    if ketat or len(clean) >= 80:
        return False
    # Cadangan seperti json_to_scribus: baris KAPITAL pendek = judul bagian
    return semua_kapital and 3 < len(stripped) <= 80 and not stripped[0].isdigit() \
        and not stripped.endswith(".")


# FIX-BAGIAN-AKHIR: judul bagian penutup sebelum REFERENCES yang ditulis penulis dengan berbagai
# cara (deklarasi penggunaan AI, ucapan terima kasih, pendanaan, konflik kepentingan, kontribusi
# penulis, ketersediaan data, etika)
RE_BAGIAN_AKHIR = re.compile(
    r"\b(DECLARATION|DECLARATIONS|DEKLARASI|PERNYATAAN|DISCLOSURE|STATEMENT|GENERATIVE|"
    r"ARTIFICIAL INTELLIGENCE|AI|GENAI|ACKNOWLEDG\w*|UCAPAN TERIMA KASIH|TERIMA KASIH|FUNDING|"
    r"PENDANAAN|CONFLICTS? OF INTEREST|COMPETING INTERESTS?|KONFLIK KEPENTINGAN|"
    r"AUTHORS?'? ?CONTRIBUTIONS?|CREDIT|KONTRIBUSI PENULIS|DATA AVAILABILITY|KETERSEDIAAN DATA|"
    r"ETHICS?|ETHICAL|ETIKA|ETIK)\b",
    re.I)


def judul_bagian_akhir(teks):
    """FIX-BAGIAN-AKHIR: True bila teks pendek (bukan kalimat) mengandung kata kunci bagian penutup."""
    t = teks.strip()
    return 0 < len(t) <= 160 and not re.search(r"[\.;,]$", t) and len(t.split()) <= 20 \
        and bool(RE_BAGIAN_AKHIR.search(t))


RE_KETERANGAN = re.compile(r"^((?:Figure|Fig\.?|Gambar|Table|Tabel)\s+\d+[\.:])\s*", re.I)


def is_caption(t):
    t = t.strip()
    return len(t) <= 600 and bool(RE_KETERANGAN.match(t))


# FIX-KET-LEMAH: keterangan tanpa tanda baca sesudah nomor ("Figure 3 Network Visualization").
# Hanya diterima bila pendek DAN bersebelahan langsung dengan gambar/tabelnya (lihat Tahap 2);
# kalimat narasi seperti "Table 1 summarizes ..." tetap paragraf.
RE_KET_LEMAH = re.compile(r"^((?:Figure|Fig\.?|Gambar|Table|Tabel)\s+\d+)\s+(?=\S)", re.I)


def is_caption_lemah(t):
    t = t.strip()
    return len(t) <= 150 and not RE_KETERANGAN.match(t) and bool(RE_KET_LEMAH.match(t))


def is_tabel_caption(t):
    return bool(re.match(r"^(Table|Tabel)\b", t.strip(), re.I))


RE_NOMOR_RUMUS = re.compile(r"^\(\s*(\d{1,3}(?:[\.\-]\d{1,3})?[a-z]?)\s*\)$")
RE_NOMOR_RUMUS_TEX = re.compile(
    r"(?:\\[ ,;:!>]|\\q?quad|~|\s|\\text\{\s*\}|&)+\(\s*(\d{1,3}(?:[\.\-]\d{1,3})?[a-z]?)\s*\)\s*$")


def rumus_teks(t):
    """FIX-RUMUS-TEKS: persamaan yang diketik sebagai teks biasa (mis. model regresi
    "ln_y = b0 + b1x1 + ... + e") -> True. Sisi kiri pendek, banyak operator, bukan kalimat."""
    t = t.strip()
    m = re.match(r"^(\S[^=]{0,40}?)\s?=\s?\S", t)
    if not m or len(t) > 900 or re.search(r"[a-z]{3,}\.\s+[A-Z]", t):
        return False
    operator = len(re.findall(r"\s[+\-−×·]\s|[+−]", t))
    kata = re.findall(r"[A-Za-z]{4,}", t)
    return operator >= 3 and len(re.findall(r"\b(the|and|of|is|are|yang|dan|adalah)\b", t)) == 0 \
        and len(kata) < 4 * operator + 4


def rumus_bernomor(inl):
    """FIX-RUMUS-BERNOMOR: paragraf yang hanya berisi satu rumus (+ nomor "(n)") -> (tex, nomor)
    atau None. Nomor boleh berupa teks sesudah rumus ("$...$ (1)", dipisah spasi/tab) atau
    berada di DALAM rumus sesudah spasi manual ("... \\quad\\quad(2)", "...\\ \\ \\ (1)").
    Rumus tanpa nomor yang berdiri sendiri dan memuat relasi/pecahan juga dijadikan display."""
    isi = [x for x in inl if x.get("t") not in ("Space", "SoftBreak", "LineBreak")
           and not (x.get("t") == "Str" and not x["c"].strip())]
    rumus = [x for x in isi if x.get("t") == "Math"]
    if len(rumus) != 1:
        return None
    tex = rumus[0]["c"][1]
    sisa = [x for x in isi if x is not rumus[0]]
    nomor = None
    if sisa:
        if isi[0] is not rumus[0]:
            return None
        teks_sisa = "".join(stringify(x) for x in sisa).strip()
        m = RE_NOMOR_RUMUS.match(teks_sisa)
        if not m:
            return None
        nomor = m.group(1)
    else:
        m = RE_NOMOR_RUMUS_TEX.search(tex)
        if m and m.start() > 0:
            nomor, tex = m.group(1), tex[:m.start()]
    if nomor is None and rumus[0]["c"][0]["t"] != "DisplayMath" and \
            not re.search(r"=|\\frac|\\sum|\\int|\\prod|\\lim|\\le|\\ge|<|>", tex):
        return None
    tex = re.sub(r"(?:\\[ ,;:!]|\\q?quad|~|\s)+$", "", tex)
    return tex, nomor


RE_PANEL = re.compile(r"\(?([a-hA-H]|[ivx]{1,3})\)\.?")


def label_panel(t):
    """FIX-PANEL-LABEL: paragraf yang hanya berisi label panel gambar, mis. "(a)", "(b) (c)",
    "a) Isometric view   b) Plan view". Hasil: daftar label, atau None."""
    t = re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()
    if not t or len(t) > 90 or not RE_PANEL.match(t) or t.endswith((".", ",", ";", ":")) and len(t) > 4:
        return None
    awal = [m.start() for m in RE_PANEL.finditer(t)
            if m.start() == 0 or t[m.start() - 1] == " "]
    if not awal or awal[0] != 0:
        return None
    # FIX-LABEL-PANEL-LIST: satu label dengan teks panjang, mis. "(a) Division of authority
    # subdomain", adalah butir daftar -- bukan label panel gambar. Label panel tunggal hanya
    # untuk teks pendek ("(a)", "a) Plan view"); deskripsi panjang berarti daftar.
    if len(awal) < 2 and len(t) > 20:
        return None
    bagian = [t[a:b].strip() for a, b in zip(awal, awal[1:] + [len(t)])]
    if any(len(x) > 40 for x in bagian):
        return None
    return bagian


def is_wawancara(inl):
    if not only_wrapped(inl, "Emph"):
        return False
    t = stringify(inl).strip()
    return t[:1] in ('"', "“")


# ======================================================================
#  Metadata halaman 1
# ======================================================================
def sentence_case(text):
    """Sama dengan _to_sentence_case Scribus, tetapi singkatan KAPITAL dipertahankan."""
    keluar = []
    for i, bag in enumerate(re.split(r"(:\s*)", text)):
        if re.match(r":\s*", bag):
            keluar.append(bag)
            continue
        kata = bag.split(" ")
        hasil = []
        for j, w in enumerate(kata):
            if len(w) > 1 and w.isupper():
                hasil.append(w)                       # ESG, SLR, ASEAN …
            elif j == 0:
                hasil.append(w[:1].upper() + w[1:].lower())
            else:
                hasil.append(w.lower())
        keluar.append(" ".join(hasil))
    return "".join(keluar)


def apa_penulis(nama_list):
    hasil = []
    for n in nama_list:
        bag = n.split()
        if len(bag) == 1:
            hasil.append(bag[0])
            continue
        inisial = []
        for p in bag[:-1]:
            if "-" in p:
                inisial.append("-".join(s[0].upper() + "." for s in p.split("-") if s))
            else:
                inisial.append(p[0].upper() + ".")
        hasil.append("{}, {}".format(bag[-1], " ".join(inisial)))
    if not hasil:
        return "Author"
    if len(hasil) == 1:
        return hasil[0]
    return "{}, & {}".format(", ".join(hasil[:-1]), hasil[-1])


def header_penulis(nama_list):
    akhir = [n.split()[-1] for n in nama_list if n.split()]
    if not akhir:
        return "Author"
    if len(akhir) == 1:
        return akhir[0]
    if len(akhir) == 2:
        return "{} & {}".format(akhir[0], akhir[1])
    return "{} et al.".format(akhir[0])


def urai_penulis(inl):
    """Baris penulis: nama (Strong/Str) + nomor afiliasi (Superscript)."""
    potongan = []          # (teks, is_super)
    def jalan(xs, sup=False):
        for x in xs:
            t = x.get("t")
            if t == "Superscript":
                potongan.append((stringify(x["c"]), True))
            elif t in ("Strong", "Emph", "Span"):
                jalan(x["c"] if t != "Span" else x["c"][1], sup)
            else:
                potongan.append((stringify(x), False))
    jalan(inl)
    penulis, nama, tanda = [], "", ""
    def simpan():
        n = re.sub(r"[\d\*†‡¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", nama).strip(" ,&")
        if n:
            penulis.append({"nama": n, "afiliasi": tanda.replace("*", "").strip(","),
                            "korespondensi": "*" in tanda})
    for teks, sup in potongan:
        if sup:
            tanda += teks
            continue
        # FIX-PENULIS-SUPERSKRIP: pemisah koma kadang ikut superskrip ("Anangkota^1,^ Billy ...") ->
        # teks nama sesudah nomor superskrip memulai penulis baru
        if tanda and nama.strip() and re.match(r"^\s*[A-Za-z\u00C0-\u024F]", teks):
            simpan()
            nama, tanda = "", ""
        bagian = re.split(r"\s*(?:,|;|&|\band\b)\s*", teks)
        for k, b in enumerate(bagian):
            if k > 0:
                simpan()
                nama, tanda = "", ""
            m = re.match(r"^(.*?)([\d\*]+)$", b)       # nomor ditulis tanpa superscript
            if m and m.group(1).strip():
                nama += m.group(1)
                tanda += m.group(2)
            else:
                nama += b
    simpan()
    return penulis


RE_INSTITUSI = re.compile(r"univ|institut|college|school|facult|fakultas|department|departemen|jurusan|"
                         r"program|stud(?:y|i)|politekni|polytechnic|acad|akademi|sekolah|hospital|rumah sakit|"
                         r"ministry|kementerian|cent(?:er|re)|laborator|research|badan|lembaga|@|abstra|"
                         r"received|keywords?|kata kunci|corresponding|street|jalan|jl\.", re.I)


def _teks_super(inl):
    hasil = []
    for x in inl or []:
        if x.get("t") == "Superscript":
            hasil.append(stringify(x["c"]))
        elif x.get("t") in ("Strong", "Emph", "Underline"):
            hasil += _teks_super(x["c"])
        elif x.get("t") == "Span":
            hasil += _teks_super(x["c"][1])
    return hasil


def _daftar_nama(t):
    """True bila teks (tanpa nomor) berupa daftar nama orang: tiap bagian 1-7 kata berhuruf kapital."""
    t = re.sub(r"[\d\*†‡¹²³⁴⁵⁶⁷⁸⁹⁰]+", " ", t)
    bagian = [b.strip() for b in re.split(r"\s*(?:,|;|&|\band\b|\bdan\b)\s*", t) if b.strip()]
    if not bagian:
        return False
    for b in bagian:
        kata = b.split()
        if not 1 <= len(kata) <= 7 or b.endswith(":"):
            return False
        if sum(1 for w in kata if w[:1].isupper()) < max(1, len(kata) - 1):
            return False
    return True


def baris_penulis(b, t, berikut=""):
    """FIX-DEPAN-TERPECAH: paragraf berisi daftar nama penulis tanpa kata institusi: bernomor
    superskrip (atau angka/bintang menempel di nama), atau tanpa nomor bila berisi >= 2 nama, atau
    satu nama yang langsung diikuti baris institusi/email."""
    if not t or len(t) > 400 or RE_INSTITUSI.search(t) or not _daftar_nama(t):
        return False
    inl = inlines_of(b) or []
    if any(re.search(r"[\d\*†‡]", s) for s in _teks_super(inl)) or \
            re.search(r"[A-Za-z][\d\*]+(?:\s*,|\s*$)", t):
        return True
    nama = [x for x in re.split(r"\s*(?:,|;|&|\band\b|\bdan\b)\s*", t) if x.strip()]
    return len(nama) >= 2 or bool(berikut and RE_INSTITUSI.search(berikut))


def lanjutan_penulis(b, t, sebelumnya=""):
    """Paragraf lanjutan baris penulis (nama yang terpotong Enter ke baris berikutnya)."""
    if not t or RE_INSTITUSI.search(t) or not _daftar_nama(t):
        return False
    return baris_penulis(b, t) or bool(re.search(r"(?:,|;|&|\band|\bdan)\s*$", sebelumnya.strip()))


RE_ABSTRAK_STRUKTUR = (r"^(Purpose|Objectives?|Aims?|Background|Introduction|Context|Tujuan|Latar\s+belakang)"
                       r"(\s+(of\s+the\s+study|and\s+\w+))?\s*:")


def urai_depan(blocks):
    """Kembalikan (meta, indeks_awal_isi). Mengikuti mesin-status word_to_json.py."""
    meta = {"judul": "", "penulis": [], "afiliasi": [], "email": "", "riwayat": {},
            "abstrak": "", "kata_kunci": []}
    # FIX-DEPAN-HEADING: paragraf bagian depan yang diberi gaya Heading oleh penulis (mis. abstrak
    # bergaya Heading 1) tetap dibaca teksnya
    teks_blok = [(i, b, stringify(inlines_of(b) or (b["c"][2] if b.get("t") == "Header" else [])))
                 for i, b in enumerate(blocks)]
    isi = [(i, b, t.strip()) for i, b, t in teks_blok if t.strip()]
    if not isi:
        return meta, 0
    # FIX-DEPAN-TERPECAH: judul boleh terdiri dari beberapa paragraf (judul + anak judul, atau judul
    # yang di-Enter) dan baris penulis boleh terpecah ke beberapa paragraf. Baris penulis dikenali dari
    # cirinya (nama + nomor superskrip, tanpa kata institusi); semua paragraf sebelumnya = judul.
    a_pen = None
    for k in range(1, min(len(isi), 7)):
        if isi[k - 1][2].rstrip().endswith(":"):
            continue                                   # sesudah "Judul:" = anak judul
        if baris_penulis(isi[k][1], isi[k][2], isi[k + 1][2] if k + 1 < len(isi) else ""):
            a_pen = k
            break
    if a_pen is not None:
        meta["judul"] = re.sub(r"\s+", " ", " ".join(x[2] for x in isi[:a_pen])).strip()
        b_pen = a_pen + 1
        while b_pen < len(isi) and b_pen - a_pen < 4 and \
                lanjutan_penulis(isi[b_pen][1], isi[b_pen][2], isi[b_pen - 1][2]):
            b_pen += 1
        inl_pen = []
        for x in isi[a_pen:b_pen]:
            if inl_pen:
                inl_pen.append({"t": "Space"})
            inl_pen.extend(inlines_of(x[1]) or [])
        meta["penulis"] = urai_penulis(inl_pen)
        pos = b_pen
    else:
        meta["judul"] = isi[0][2]
        pos = 1
        if len(isi) > 1 and "univ" not in isi[1][2].lower() and "abstract" not in isi[1][2].lower():
            meta["penulis"] = urai_penulis(inlines_of(isi[1][1]) or [])
            pos = 2
    mode, abstrak = "afiliasi", []
    for k in range(pos, len(isi)):
        i, b, t = isi[k]
        if t.lower() in SAMPAH:
            continue
        # FIX-KATA-KUNCI-VARIAN: Keywords / Key words / Keyword / Kata kunci / Kata-kata kunci;
        # bila label berdiri sendiri, kata kunci diambil dari paragraf berikutnya.
        POLA_KK = r"^(key[\s\-]*words?|kata[\s\-]*(?:kata[\s\-]*)?kunci)\b[\s:\.\-–—]*"
        if re.match(POLA_KK, t, re.I):
            kw = re.sub(POLA_KK, "", t, flags=re.I)
            return_index = i + 1
            if not kw.strip() and k + 1 < len(isi):
                kw, return_index = isi[k + 1][2], isi[k + 1][0] + 1
            meta["kata_kunci"] = [x.strip().rstrip(".") for x in re.split(r"[;,]", kw) if x.strip()]
            meta["abstrak"] = "\n\n".join(abstrak)
            return meta, return_index
        if is_header(t, ketat=True):
            meta["abstrak"] = "\n\n".join(abstrak)
            return meta, i
        # FIX-ABSTRAK-BARIS: "Abstract"/"ABSTRAK" di baris sendiri (tanpa titik dua) juga penanda abstrak
        m_abs = re.search(r"(Abstract|Abstrak)\s*[:\.]", t, re.I) or \
            re.fullmatch(r"(Abstract|Abstrak)\s*[:\.]?", t, re.I)
        # FIX-ABSTRAK-TERSTRUKTUR: abstrak tanpa judul "Abstract" yang langsung dimulai dengan
        # sub-judul terstruktur ("Purpose:", "Background:", "Objectives:", "Tujuan:", ...)
        if mode == "afiliasi" and not m_abs and re.match(RE_ABSTRAK_STRUKTUR, t):
            abstrak.append(t)
            mode = "abstrak"
            continue
        if mode == "afiliasi" and m_abs:
            sisa = t[m_abs.end():].strip()
            if sisa:
                abstrak.append(sisa)
            mode = "abstrak"
            continue
        if mode == "abstrak":
            abstrak.append(re.sub(r"^(Abstract|Abstrak)\s*[:\.]?\s*", "", t, flags=re.I))
            continue
        # mode afiliasi: afiliasi / email / riwayat
        m_hist = re.search(r"Received\s*:", t, re.I)
        if m_hist:
            h = t[m_hist.start():]
            for kunci, pola in (("diterima", r"Received\s*:\s*([^;]+)"),
                                ("disetujui", r"Accepted\s*:\s*([^;]+)"),
                                ("terbit", r"(?:Online\s+)?Published\s*:\s*([^;]+)")):
                mm = re.search(pola, h, re.I)
                if mm:
                    meta["riwayat"][kunci] = mm.group(1).strip().rstrip(".")
            continue
        m_mail = re.search(r"[\w\.\-]+@[\w\.\-]+\.\w+", t)
        if m_mail and re.search(r"corresponding|e-?mail", t, re.I):
            meta["email"] = m_mail.group(0)
            continue
        # FIX-NOMOR-AFILIASI: nomor afiliasi ("1Postgraduate ...", "2 Master ...") disimpan
        m_no = re.match(r"^\s*(\d{1,2}|[a-e])(?=\s|[A-Z])\s*", t)
        meta.setdefault("afiliasi_nomor", []).append(m_no.group(1) if m_no else "")
        meta["afiliasi"].append(re.sub(r"^[\d\*]+\s*", "", t).replace("*", "").strip())
    meta["abstrak"] = "\n\n".join(abstrak)
    return meta, len(blocks)


# ======================================================================
#  Transformasi isi
# ======================================================================
def penanda_daftar(gaya, nomor):
    if gaya == "LowerAlpha":
        return chr(96 + nomor) + "."
    if gaya == "UpperAlpha":
        return chr(64 + nomor) + "."
    if gaya in ("LowerRoman", "UpperRoman"):
        nilai = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                 (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
        r, n = "", nomor
        for v, s in nilai:
            while n >= v:
                r += s
                n -= v
        return (r.lower() if gaya == "LowerRoman" else r) + "."
    return str(nomor) + "."


def gabung_inline_item(item_blocks):
    out = []
    for b in item_blocks:
        inl = inlines_of(b)
        if inl is None:
            continue
        if out:
            out.append({"t": "Space"})
        out.extend(inl)
    return out


def ganti_emf(path):
    """FIX-EMF: jalur gambar EMF/WMF -> PNG hasil ubah_emf."""
    return re.sub(r"\.(?:emf|wmf)$", ".png", path, flags=re.I)


def bagian_gambar(img, workdir):
    """(jalur, lebar pt, tinggi pt) satu elemen Image pandoc; tinggi dari atribut Word, atau dari
    rasio piksel bila tidak ada."""
    attr = dict(img["c"][0][2])
    path = img["c"][2][0]
    w = ukuran_ke_pt(attr.get("width", "")) or 0.0
    h = ukuran_ke_pt(attr.get("height", "")) or 0.0
    if w and not h:
        try:
            from PIL import Image
            with Image.open(os.path.join(workdir, ganti_emf(path))) as im:
                h = w * im.size[1] / float(im.size[0])
        except Exception:
            h = w * 0.6
    return path, w, h


def font_label_panel(ukuran_px):
    """FIX-PANEL-LABEL: font serif untuk label panel (a)/(b) yang digambar ke PNG gabungan."""
    from PIL import ImageFont
    calon = []
    for d in (os.path.join(BASE, "fonts"), "/usr/share/fonts", "C:/Windows/Fonts"):
        if os.path.isdir(d):
            for akar, _, berkas in os.walk(d):
                for b in berkas:
                    if b.lower() in ("times.ttf", "liberationserif-regular.ttf", "dejavuserif.ttf"):
                        calon.append((("times.ttf", "liberationserif-regular.ttf", "dejavuserif.ttf")
                                      .index(b.lower()), os.path.join(akar, b)))
    for _, f in sorted(calon):
        try:
            return ImageFont.truetype(f, ukuran_px)
        except Exception:
            pass
    try:
        return ImageFont.load_default(ukuran_px)
    except TypeError:
        return ImageFont.load_default()


def susun_gambar(baris, workdir, nomor, label_baris=None, slot_pt=None):
    """FIX-GAMBAR-GABUNG: gabungkan beberapa gambar menjadi satu PNG 300 dpi sesuai ukuran
    tampil di Word: tiap baris = gambar berdampingan (jarak 6 pt), baris-baris ditumpuk
    (jarak 4 pt) dan diletakkan di tengah. Hasil: jalur relatif terhadap workdir.
    FIX-PANEL-LABEL: label_baris {indeks baris: ["(a)", "(b)"]} digambar di bawah baris itu
    (satu label per gambar bila jumlahnya sama, selain itu satu baris di tengah); ukurannya
    dihitung agar tampil +-9.5 pt setelah gambar diskalakan ke lebar slot_pt."""
    from PIL import Image, ImageDraw
    label_baris = label_baris or {}
    px = lambda pt: max(1, int(round(pt * 300 / 72.0)))
    lebar = max(sum(w for _, w, _ in r) + 6.0 * (len(r) - 1) for r in baris)
    fs = 9.5 * (lebar / slot_pt if slot_pt else 1.0)
    fs = min(max(fs, 7.0), 24.0)
    t_lbl = fs * 1.35
    tinggi = sum(max(h for _, _, h in r) for r in baris) + 4.0 * (len(baris) - 1) + \
        sum(t_lbl for k in range(len(baris)) if label_baris.get(k))
    kanvas = Image.new("RGB", (px(lebar), px(tinggi)), "white")
    pena = ImageDraw.Draw(kanvas)
    font = font_label_panel(px(fs)) if label_baris else None

    def tulis(teks, x_tengah, y_atas):
        try:
            l, t, r_, b = pena.textbbox((0, 0), teks, font=font)
        except AttributeError:
            (r_, b), l, t = pena.textsize(teks, font=font), 0, 0
        pena.text((px(x_tengah) - (r_ - l) / 2 - l, px(y_atas) + (px(t_lbl) - (b - t)) / 2 - t),
                  teks, fill="black", font=font)
    y = 0.0
    for k, r in enumerate(baris):
        lebar_r = sum(w for _, w, _ in r) + 6.0 * (len(r) - 1)
        x = (lebar - lebar_r) / 2
        tinggi_r = max(h for _, _, h in r)
        pusat = []
        for path, w, h in r:
            with Image.open(os.path.join(workdir, ganti_emf(path))) as im:
                im = im.convert("RGBA")
                lapis = Image.new("RGBA", im.size, "white")
                lapis.alpha_composite(im)
                kanvas.paste(lapis.convert("RGB").resize((px(w), px(h)), Image.LANCZOS),
                             (px(x), px(y + (tinggi_r - h) / 2)))
            pusat.append(x + w / 2)
            x += w + 6.0
        y += tinggi_r
        lbl = label_baris.get(k)
        if lbl:
            if len(lbl) == len(r):
                for teks, xc in zip(lbl, pusat):
                    tulis(teks, xc, y)
            else:
                tulis("   ".join(lbl), lebar / 2, y)
            y += t_lbl
        y += 4.0
    keluar = os.path.join("media", "gabung_{}.png".format(nomor))
    kanvas.save(os.path.join(workdir, keluar), dpi=(300, 300))
    return keluar


TANPA_INDENT = set()      # FIX-INDENT-LANJUTAN: awalan teks paragraf Word tanpa indent baris pertama


def kunci_paragraf(teks):
    return re.sub(r"[^a-z0-9]", "", teks.lower())[:60]


def baca_indent_word(docx):
    """FIX-INDENT-LANJUTAN: awalan teks (kunci_paragraf) semua paragraf docx yang TIDAK memiliki
    indent baris pertama (w:ind firstLine pada paragraf, atau pada style-nya bila tidak ada)."""
    import zipfile
    hasil = set()
    try:
        z = zipfile.ZipFile(docx)
        doc = z.read("word/document.xml").decode("utf-8")
        gaya = z.read("word/styles.xml").decode("utf-8")
    except Exception:
        return hasil
    ind_gaya = {}
    for m in re.finditer(r'<w:style [^>]*w:styleId="([^"]+)".*?</w:style>', gaya, flags=re.S):
        mi = re.search(r'<w:ind [^>]*w:firstLine="(\d+)"', m.group(0))
        ind_gaya[m.group(1)] = int(mi.group(1)) if mi else 0
    for p in re.findall(r"<w:p[ >].*?</w:p>", doc, flags=re.S):
        teks = "".join(re.findall(r"<w:t[^>]*>([^<]*)", p))
        if not teks.strip():
            continue
        ppr = re.search(r"<w:pPr>.*?</w:pPr>", p, flags=re.S)
        ppr = ppr.group(0) if ppr else ""
        mi = re.search(r"<w:ind [^>]*>", ppr)
        if mi and ("w:firstLine=" in mi.group(0) or "w:hanging=" in mi.group(0)):
            m2 = re.search(r'w:firstLine="(\d+)"', mi.group(0))
            indent = int(m2.group(1)) if m2 else 0
        else:
            ms = re.search(r'<w:pStyle w:val="([^"]+)"', ppr)
            indent = ind_gaya.get(ms.group(1) if ms else "Normal", ind_gaya.get("Normal", 0))
        if indent < 20:
            hasil.add(kunci_paragraf(teks))
    return hasil


def _buka_tabel_pembungkus(blk):
    """Buka tabel 1-sel pembungkus layout Word yang menyembunyikan tabel isi di dalamnya."""
    if not isinstance(blk, dict) or blk.get("t") != "Table":
        return None
    c = blk["c"]
    baris = list(c[3][1]) + [r for bd in c[4] for r in list(bd[2]) + list(bd[3])] + list(c[5][1])
    sel_isi = []
    for row in baris:
        for cell in row[1]:
            non_kosong = [x for x in cell[4] if x.get("t") not in ("Para", "Plain") or stringify(x).strip()]
            if non_kosong:
                sel_isi.append(non_kosong)
    if len(sel_isi) != 1:
        return None
    dalam_tabel = [x for x in sel_isi[0] if x.get("t") == "Table"]
    if len(dalam_tabel) != 1:
        return None
    inner = dalam_tabel[0]
    if c[1][1] and not inner["c"][1][1]:
        inner["c"][1][1] = c[1][1]
    return sel_isi[0]


def ubah_isi(blocks, workdir, laporan, di_tempat=False):
    """Kembalikan daftar blok baru + statistik untuk QA."""
    # --- Tahap 1: daftar item berjenis ---
    # FIX-DIV-BUKA: paragraf yang dibungkus Div (kontrol konten/gaya khusus Word) diproses
    # seperti paragraf biasa
    def buka_div(bs):
        hasil = []
        for x in bs:
            if x.get("t") == "Div":
                hasil.extend(buka_div(x["c"][1]))
            elif x.get("t") == "Table":
                buka = _buka_tabel_pembungkus(x)
                if buka is not None:
                    hasil.extend(buka_div(buka))
                else:
                    hasil.append(x)
            else:
                hasil.append(x)
        return hasil

    # FIX-JUDUL-DALAM-PARAGRAF: judul yang diketik dalam paragraf lain lewat Shift+Enter
    # (mis. "...ucapan terima kasih.<br><br>**DECLARATION OF GENERATIVE AI ...**") dipisah
    # menjadi paragraf sendiri agar dikenali sebagai judul
    def segmen_judul(seg):
        isi = [x for x in seg if x.get("t") not in ("Space", "SoftBreak")]
        if not isi:
            return False
        teks = teks_judul(seg)
        tebal = all(x.get("t") == "Strong" for x in isi)
        kapital = teks == teks.upper()
        if is_header(teks, ketat=True) and (tebal or kapital):
            return True
        return tebal and judul_bagian_akhir(bersih_nomor(teks))

    def pecah_judul(bs):
        hasil = []
        for x in bs:
            if x.get("t") not in ("Para", "Plain") or not any(i.get("t") == "LineBreak" for i in x["c"]):
                hasil.append(x)
                continue
            segs = [[]]
            for i in x["c"]:
                if i.get("t") == "LineBreak":
                    segs.append([])
                else:
                    segs[-1].append(i)
            if not any(segmen_judul(s) for s in segs):
                hasil.append(x)
                continue
            kumpul = []

            def tutup():
                while kumpul and kumpul[-1].get("t") in ("LineBreak", "Space", "SoftBreak"):
                    kumpul.pop()
                while kumpul and kumpul[0].get("t") in ("LineBreak", "Space", "SoftBreak"):
                    kumpul.pop(0)
                if kumpul:
                    hasil.append({"t": x["t"], "c": list(kumpul)})
                kumpul.clear()
            for n, s in enumerate(segs):
                if segmen_judul(s):
                    tutup()
                    hasil.append({"t": "Para", "c": s})
                else:
                    if n and kumpul:
                        kumpul.append({"t": "LineBreak"})
                    kumpul.extend(s)
            tutup()
        return hasil
    blocks = pecah_judul(buka_div(blocks))
    items = []
    for b in blocks:
        t = b.get("t")
        if t == "Table":
            # FIX-KET-TABEL-PANDOC: keterangan Word yang dikenali pandoc sebagai caption tabel
            # dijadikan item keterangan biasa (di atas tabel), caption pandoc dikosongkan.
            ket_pd = stringify([x for blk in (b["c"][1][1] or []) for x in (inlines_of(blk) or [])]).strip()
            if ket_pd and is_caption(ket_pd):
                inl_pd = [x for blk in b["c"][1][1] for x in (inlines_of(blk) or [])]
                items.append({"jenis": "ket", "teks": ket_pd, "inl": inl_pd})
                b["c"][1] = [None, []]
            items.append({"jenis": "tabel", "blok": b})
            continue
        if t == "Figure":
            # FIX-BLOK-FIGURE: blok Figure pandoc (gambar + keterangan Word yang dikenali pandoc)
            baris_f = [[bagian_gambar(x, workdir) for x in (inlines_of(blk) or []) if x.get("t") == "Image"]
                       for blk in b["c"][2]]
            baris_f = [r for r in baris_f if r]
            inl_f = [x for blk in (b["c"][1][1] or []) for x in (inlines_of(blk) or [])]
            if baris_f:
                items.append({"jenis": "gambar", "path": baris_f[0][0][0], "baris": baris_f, "ket": None,
                              "lebar": max(sum(w for _, w, _ in r) + 6.0 * (len(r) - 1) for r in baris_f)})
            if inl_f and is_caption(stringify(inl_f).strip()):
                items.append({"jenis": "ket", "teks": stringify(inl_f).strip(), "inl": inl_f})
            elif inl_f:
                items.append({"jenis": "para", "inl": inl_f, "blok": {"t": "Para", "c": inl_f}})
            continue
        if t == "Header":
            lvl, inl = b["c"][0], b["c"][2]
            teks = teks_judul(inl)
            # FIX-HEADER-ISI: style Heading pada paragraf gambar / keterangan / kosong
            # (artikel 388) bukan judul bagian -> proses sebagai paragraf biasa.
            if any(x.get("t") == "Image" for x in inl) or not teks or is_caption(teks) \
                    or is_caption_lemah(teks):
                b, t = {"t": "Para", "c": inl}, "Para"
        if t == "Header":
            lvl, inl = b["c"][0], b["c"][2]
            teks = teks_judul(inl)
            if lvl == 1 or is_header(teks):
                items.append({"jenis": "bagian", "teks": bersih_nomor(teks).upper()})
            else:
                # FIX-HEADING-L4: Heading 4+ Word (sub-sub-sub bagian) dipertahankan sebagai
                # tingkatnya sendiri agar spasi antarjudul bertingkat tidak disisipi #celah().
                items.append({"jenis": "sub" if lvl == 2 else
                              ("subsub" if lvl == 3 else "subsubsub"), "inl": inl})
            continue
        if t in ("OrderedList", "BulletList"):
            # FIX-DAFTAR-JUDUL: judul bagian yang diketik sebagai item List Paragraph
            # (artikel 388: INTRODUCTION, LITERATURE REVIEW, RESEARCH METHODS)
            daftar_item = b["c"][1] if t == "OrderedList" else b["c"]
            teks_item = [stringify(gabung_inline_item(it)).strip() for it in daftar_item]
            if teks_item and all(x and is_header(x, ketat=True) for x in teks_item):
                for x in teks_item:
                    items.append({"jenis": "bagian", "teks": bersih_nomor(x).upper()})
                continue
            # FIX-PANEL-DAFTAR: label panel "(a) (b)" yang dibaca pandoc sebagai daftar huruf
            if t == "OrderedList" and b["c"][0][1]["t"] in ("LowerAlpha", "UpperAlpha", "LowerRoman"):
                tanda = [penanda_daftar(b["c"][0][1]["t"], b["c"][0][0] + n).rstrip(".") for n in range(len(teks_item))]
                gabung_lbl = " ".join("({}) {}".format(tn, tx).strip() for tn, tx in zip(tanda, teks_item))
                lbl_p = label_panel(gabung_lbl)
                if lbl_p:
                    items.append({"jenis": "panel_lbl", "label": lbl_p,
                                  "inl": [{"t": "Str", "c": gabung_lbl}],
                                  "blok": {"t": "Para", "c": [{"t": "Str", "c": gabung_lbl}]}})
                    continue
            items.append({"jenis": "daftar", "blok": b})
            continue
        # ==========================================================
        # TAMBAHKAN 3 BARIS INI:
        # Ubah BlockQuote dari Word langsung menjadi panggilan #wawancara
        if t == "BlockQuote":
            inl_q = gabung_inline_item(b["c"])
            teks_q = stringify(inl_q).strip()
            # FIX-KET-KUTIPAN: keterangan "Table 3. ..." yang ber-indent (dibaca pandoc sebagai
            # kutipan) tetap keterangan
            if is_caption(teks_q) or is_caption_lemah(teks_q):
                items.append({"jenis": "ket", "teks": teks_q, "inl": inl_q,
                              "lemah": not is_caption(teks_q), "blok": {"t": "Para", "c": inl_q}})
                continue
            if rumus_teks(teks_q):                      # FIX-RUMUS-TEKS
                items.append({"jenis": "rumus_teks", "inl": unwrap(inl_q, "Emph")})
                continue
            # FIX-ACK-BLOCKQUOTE: pandoc kadang membaca judul ber-indent sebagai BlockQuote.
            # Jika teksnya adalah judul bagian yang sah, jadikan bagian, bukan wawancara.
            if is_header(teks_q, ketat=True) or judul_bagian_akhir(bersih_nomor(teks_q)):
                items.append({"jenis": "bagian", "teks": bersih_nomor(teks_q).upper(), "asli": teks_q, "inl": inl_q})
                continue

            items.append({"jenis": "wawancara", "inl": inl_q})
            continue
        # ==========================================================
        inl = inlines_of(b)
        if inl is None:
            items.append({"jenis": "lain", "blok": b})
            continue
        teks = stringify(inl).strip()
        gambar = [x for x in inl if x.get("t") == "Image"]
        if gambar:
            # FIX-PANEL: semua gambar dalam satu paragraf = satu baris panel (berdampingan)
            baris_g = [bagian_gambar(x, workdir) for x in gambar]
            items.append({"jenis": "gambar", "path": gambar[0]["c"][2][0],
                          "lebar": sum(x[1] for x in baris_g) + 6.0 * (len(baris_g) - 1),
                          "baris": [baris_g], "ket": None})
            sisa = [x for x in inl if x.get("t") != "Image"]
            teks_sisa = stringify(sisa).strip()
            if teks_sisa and is_caption(teks_sisa):          # FIX-KET-SEPARAGRAF
                items.append({"jenis": "ket", "teks": teks_sisa, "inl": sisa})
            elif teks_sisa:
                items.append({"jenis": "para", "inl": sisa, "blok": {"t": "Para", "c": sisa}})
            continue
        if not teks or teks.lower() in SAMPAH:
            continue
        rb = rumus_bernomor(inl)
        if rb:                                          # FIX-RUMUS-BERNOMOR
            items.append({"jenis": "rumus", "tex": rb[0], "nomor": rb[1], "inl": inl})
            continue
        if rumus_teks(teks) and not any(x.get("t") == "Math" for x in inl):   # FIX-RUMUS-TEKS
            items.append({"jenis": "rumus_teks", "inl": unwrap(inl, "Emph")})
            continue
        lbl_panel = label_panel(teks)
        if lbl_panel:                                   # FIX-PANEL-LABEL: "(a)", "(b) (c)"
            items.append({"jenis": "panel_lbl", "label": lbl_panel, "inl": inl, "blok": b})
            continue
        if is_caption(teks):
            items.append({"jenis": "ket", "teks": teks, "inl": inl})
            continue
        # FIX-KET-CATATAN: keterangan panjang yang memuat "Note." di paragraf yang sama ->
        # keterangan + paragraf catatan terpisah
        if RE_KETERANGAN.match(teks) and len(teks) > 300:
            pisah = next((k for k, x in enumerate(inl) if k > 0 and
                          re.match(r"(Note|Notes|Catatan|Source|Sumber)\s*[\.:]", stringify(x).strip())), None)
            if pisah and is_caption(stringify(inl[:pisah]).strip()):
                items.append({"jenis": "ket", "teks": stringify(inl[:pisah]).strip(), "inl": inl[:pisah]})
                items.append({"jenis": "para", "inl": inl[pisah:], "blok": {"t": "Para", "c": inl[pisah:]}})
                continue
        if is_caption_lemah(teks):
            items.append({"jenis": "ket", "teks": teks, "inl": inl, "lemah": True, "blok": b})
            continue
        # FIX-JUDUL-POLOS: nama bab baku (Introduction, Methods, ...) dikenali walau tidak tebal
        teks_j = teks_judul(inl)                          # FIX-JUDUL-BERSIH
        if is_header(teks_j) and (only_wrapped(inl, "Strong") or teks_j == teks_j.upper()) \
                or is_header(teks_j, ketat=True):
            items.append({"jenis": "bagian", "teks": bersih_nomor(teks_j).upper(), "asli": teks_j, "inl": inl})
            continue
        if only_wrapped(inl, "Strong") and len(teks) < 150 and not teks.endswith("."):
            items.append({"jenis": "sub", "inl": unwrap(inl, "Strong")})
            continue
        # FIX-JUDUL-POLOS: sub-bab bernomor "2.1 Judul" / "2.1.3 Judul" walau tidak tebal
        m_sub = RE_NOMOR_SUB.match(teks)
        if m_sub and len(teks) < 150 and not teks.endswith(".") and teks[len(m_sub.group(0)):][:1].isupper():
            items.append({"jenis": "sub" if m_sub.group(0).strip().rstrip(".").count(".") == 1 else "subsub",
                          "inl": inl})
            continue
        if is_wawancara(inl):
            items.append({"jenis": "wawancara", "inl": unwrap(inl, "Emph")})
            continue
        if only_wrapped(inl, "Emph") and len(teks) < 150 and not teks.endswith("."):
            items.append({"jenis": "subsub", "inl": unwrap(inl, "Emph")})
            continue
        # FIX-QA-SUB-POLOS: baris pendek tanpa tanda baca akhir, tidak tebal/miring/bernomor
        # = kemungkinan sub-bab tanpa format -> ditahan QA (tidak ditebak)
        polos = (3 <= len(teks) <= 100 and len(teks.split()) <= 12 and teks[:1].isupper()
                 and not re.search(r"[\.\?!:;,]$", teks))
        items.append({"jenis": "para", "inl": inl, "blok": b, "calon_sub": polos})

    # --- Tahap 1.15 (FIX-SUB-NAMA-BAKU): "Result"/"Discussion" huruf campuran tepat di bawah bagian
    # KAPITAL "RESULT AND DISCUSSION" adalah sub-bab (bukan bagian kedua), bila judul bagian naskah
    # ditulis kapital.
    kapital = sum(1 for it in items if it["jenis"] == "bagian" and it.get("asli", it["teks"]) ==
                  it.get("asli", it["teks"]).upper())
    induk = None
    for k, it in enumerate(items):
        if it["jenis"] != "bagian":
            continue
        asli = it.get("asli")
        if asli and asli != asli.upper() and kapital >= 2 and induk and it["teks"] != induk \
                and re.search(r"\b{}\b".format(re.escape(it["teks"])), induk):
            items[k] = {"jenis": "sub", "inl": unwrap(it["inl"], "Strong")}
            continue
        induk = it["teks"]
    # --- Tahap 1.2 (FIX-BAGIAN-AKHIR): judul penutup sebelum REFERENCES (deklarasi AI, ucapan terima
    # kasih, pendanaan, ...) yang dibaca sebagai sub-bab/paragraf tebal -> judul bagian. Hanya di
    # antara bagian KESIMPULAN (atau bagian ke-3 dst.) dan REFERENCES.
    i_ref = next((k for k, it in enumerate(items)
                  if it["jenis"] == "bagian" and it["teks"] in BAGIAN_REFERENSI), None)
    if i_ref is not None:
        i_kes = max((k for k, it in enumerate(items[:i_ref]) if it["jenis"] == "bagian" and
                     any(it["teks"].startswith(x) for x in ("CONCLUSION", "KESIMPULAN", "PENUTUP", "SIMPULAN",
                                                             "CLOSING", "DISCUSSION", "PEMBAHASAN"))),
                    default=None)
        if i_kes is not None:
            for k in range(i_kes + 1, i_ref):
                it = items[k]
                if it["jenis"] not in ("sub", "subsub", "subsubsub", "para") or not it.get("inl"):
                    continue
                tj = teks_judul(it["inl"])
                tebal = it["jenis"] in ("sub", "subsub", "subsubsub") or only_wrapped(it["inl"], "Strong") \
                    or (tj == tj.upper() and any(c.isalpha() for c in tj))
                if tebal and judul_bagian_akhir(tj):
                    items[k] = {"jenis": "bagian", "teks": bersih_nomor(tj).upper()}

    # --- Tahap 1.3 (FIX-KET-APA): label "Table 2" / "Figure 3" di paragraf sendiri + judul di
    # paragraf berikutnya (gaya APA 7) -> satu keterangan "Table 2. Judul"
    RE_LABEL_SAJA = re.compile(r"^((?:Figure|Fig\.?|Gambar|Table|Tabel)\s+\d+)\s*[\.:]?$", re.I)
    k = 0
    while k + 1 < len(items):
        it, nx = items[k], items[k + 1]
        m_l = RE_LABEL_SAJA.match(stringify(it.get("inl") or []).strip()) \
            if it["jenis"] in ("para", "sub", "subsub", "subsubsub", "ket") else None
        if m_l and nx["jenis"] in ("para", "sub", "subsub", "subsubsub") and nx.get("inl"):
            judul = unwrap(unwrap(nx["inl"], "Emph"), "Strong")
            t_judul = stringify(judul).strip()
            if 0 < len(t_judul) <= 250 and not is_caption(t_judul):
                inl_k = [{"t": "Str", "c": m_l.group(1) + "."}, {"t": "Space"}] + judul
                items[k] = {"jenis": "ket", "teks": stringify(inl_k).strip(), "inl": inl_k}
                del items[k + 1]
        k += 1

    # --- Tahap 1.4 (FIX-TABEL-PANEL): "Table 2." + "Panel A. ..." + tabel + "Panel B. ..." + tabel
    # = satu tabel berpanel: judul panel ikut di dalam objek tabel, tabel-tabel panel digabung.
    RE_PANEL_TBL = re.compile(r"^(Panel|Bagian)\s+([A-Z]|\d+|[IVX]+)\b")
    for k, it in enumerate(items):
        if it["jenis"] in ("para", "sub", "subsub", "subsubsub") and k + 1 < len(items) and \
                items[k + 1]["jenis"] == "tabel":
            tk = stringify(it.get("inl") or []).strip()
            if len(tk) < 120 and RE_PANEL_TBL.match(tk):
                it["jenis"] = "panel_tbl"
    rapat = []
    for k, it in enumerate(items):
        if it["jenis"] == "panel_tbl":
            items[k + 1]["judul_panel"] = it["inl"]
            continue
        if it["jenis"] == "tabel" and it.get("judul_panel") and rapat and rapat[-1]["jenis"] == "tabel" \
                and (rapat[-1].get("judul_panel") or rapat[-1].get("panel")):
            rapat[-1].setdefault("panel", []).append(it)
            continue
        rapat.append(it)
    items = rapat

    # --- Tahap 1.5 (FIX-GAMBAR-GABUNG) ---
    # Gambar yang berurutan tanpa teks di antaranya (gambar + strip "Note." EMF, panel bertumpuk)
    # digabung menjadi SATU gambar; strip tipis tepat sesudah tabel = catatan tabel itu.
    gabung = []
    for it in items:
        sebelumnya = gabung[-1] if gabung else None
        if it["jenis"] == "panel_lbl":
            # FIX-PANEL-LABEL: label panel di bawah satu baris gambar -> ikut digambar ke PNG
            if sebelumnya is not None and sebelumnya["jenis"] == "gambar" and \
                    len(sebelumnya["baris"]) - 1 not in sebelumnya.setdefault("label_baris", {}):
                sebelumnya["label_baris"][len(sebelumnya["baris"]) - 1] = it["label"]
                continue
            it = {"jenis": "para", "inl": it["inl"], "blok": it["blok"]}
        if it["jenis"] == "gambar" and sebelumnya is not None:
            tipis = all(h / w < 0.25 for (_, w, h) in it["baris"][0] if w)
            if sebelumnya["jenis"] == "gambar":
                sebelumnya["baris"] += it["baris"]
                sebelumnya["lebar"] = max(sebelumnya["lebar"], it["lebar"])
                continue
            if sebelumnya["jenis"] == "tabel" and tipis:
                sebelumnya.setdefault("catatan", []).extend(p for (p, _, _) in it["baris"][0])
                continue
        gabung.append(it)
    items = gabung

    # --- Tahap 2: pasangkan keterangan dengan gambar/tabel ---
    for k, it in sorted(enumerate(items), key=lambda kv: bool(kv[1].get("lemah"))):
        if it["jenis"] != "ket":
            continue
        tabel = is_tabel_caption(it["teks"])
        urutan = range(k + 1, min(k + 4, len(items))) if tabel else \
            list(range(k - 1, max(k - 4, -1), -1)) + list(range(k + 1, min(k + 3, len(items))))
        if it.get("lemah"):
            urutan = [j for j in ([k + 1] if tabel else [k - 1, k + 1]) if 0 <= j < len(items)]
        for j in urutan:
            sasaran = items[j]
            if sasaran["jenis"] == ("tabel" if tabel else "gambar") and not sasaran.get("ket"):
                sasaran["ket"] = it
                it["jenis"] = "terpakai"
                break
            if sasaran["jenis"] in ("bagian", "sub"):
                break
        if it["jenis"] == "ket":
            # Tabel yang dikirim penulis sebagai gambar: "Table N." di atas sebuah gambar
            for j in range(k + 1, min(k + (2 if it.get("lemah") else 3), len(items))):
                if items[j]["jenis"] == "gambar" and not items[j].get("ket"):
                    items[j]["ket"] = it
                    it["jenis"] = "terpakai"
                    break
        if it["jenis"] == "ket" and it.get("lemah"):
            it["jenis"] = "para"            # bukan keterangan: kalimat biasa
            continue
        if it["jenis"] == "ket":
            laporan["masalah"].append("Keterangan tanpa gambar/tabel: " + it["teks"][:60])
            it["jenis"] = "para"
            it["blok"] = {"t": "Para", "c": it["inl"]}

    # FIX-CATATAN-OBJEK: paragraf "Note." / "Source:" / "Sumber:" tepat sesudah gambar/tabel
    # (sesudah keterangannya) menjadi catatan objek itu, bukan paragraf narasi.
    RE_CATATAN = re.compile(r"^((Notes?|Source|Sources|Sumber|Catatan|Keterangan)\s*[\.:]|\*+\s*\S|[a-c†‡]\s?[\)\.]?\s*p\s*[<>=])", re.I)
    k = 0
    while k < len(items):
        it = items[k]
        if it["jenis"] in ("gambar", "tabel"):
            j = k + 1
            while j < len(items) and items[j]["jenis"] == "terpakai":
                j += 1
            while j < len(items) and items[j]["jenis"] == "para" and \
                    RE_CATATAN.match(stringify(items[j]["inl"]).strip()) and \
                    len(stringify(items[j]["inl"])) < 1500:
                it.setdefault("catatan_teks", []).append(
                    [{"t": "Space"} if x.get("t") in ("LineBreak", "SoftBreak") else x for x in items[j]["inl"]])
                items[j]["jenis"] = "terpakai"
                j += 1
        k += 1

    # FIX-LABEL-GANTUNG: >= 2 paragraf berurutan berlabel sama ("QA1: ...", "H1: ...", "RQ2: ...")
    # menjadi daftar berlabel dengan tab stop 25pt dan baris lanjutan menggantung.
    RE_LABEL = re.compile(r"^([A-Za-z]{1,4})(\d{1,2}[a-z]?)\s*:\s+(?=\S)")
    k = 0
    while k < len(items):
        m0 = RE_LABEL.match(stringify(items[k].get("inl") or []).strip()) if items[k]["jenis"] == "para" else None
        if not m0:
            k += 1
            continue
        j = k
        while j < len(items) and items[j]["jenis"] == "para":
            mm = RE_LABEL.match(stringify(items[j]["inl"]).strip())
            if not mm or mm.group(1) != m0.group(1):
                break
            j += 1
        if j - k >= 2:
            for it in items[k:j]:
                mentah = stringify(it["inl"])
                mm = RE_LABEL.match(mentah.strip())
                it["jenis"], it["label"] = "berlabel", mm.group(1) + mm.group(2) + ":"
                it["inl_isi"] = drop_prefix(it["inl"], len(mentah) - len(mentah.lstrip()) + len(mm.group(0)))
        k = max(j, k + 1)

    # FIX-REF-TAHUN-DOI: gabung entri referensi yang terpotong karena tahun hanya muncul di
    # dalam URL/DOI, dan bersihkan LineBreak tengah kalimat ("These preconditions are:" +
    # "cohesive customary authority...").
    di_ref_awal = False
    prev_ref = None
    for it in items:
        if it["jenis"] == "bagian":
            di_ref_awal = it["teks"] in BAGIAN_REFERENSI
            prev_ref = None
            continue
        if it["jenis"] != "para" or not it.get("inl"):
            prev_ref = None
            continue
        if di_ref_awal:
            t_bersih = re.sub(r"https?://\S+|10\.\d{4,9}/\S+", "", stringify(it["inl"])).strip()
            if prev_ref is not None and not RE_REF_TAHUN.search(t_bersih):
                prev_ref["inl"].extend([{"t": "Space"}] + it["inl"])
                it["jenis"] = "terpakai"
                continue
            prev_ref = it
        else:
            inl_baru = []
            for idx_i, tok in enumerate(it["inl"]):
                if tok.get("t") == "LineBreak":
                    sisa_t = stringify(it["inl"][idx_i + 1:]).lstrip()
                    if sisa_t and sisa_t[0].islower():
                        inl_baru.append({"t": "Space"})
                        continue
                inl_baru.append(tok)
            it["inl"] = inl_baru
            if it.get("blok") and it["blok"].get("t") in ("Para", "Plain"):
                it["blok"]["c"] = inl_baru

    # --- Tahap 3: keluarkan blok ---
    # FIX-DI-TEMPAT: objek lebar memutus #kolom[...] agar tetap di posisi naskah
    out = [raw_b("#kolom[")] if di_tempat else []

    def putus_kolom(fungsi_objek):
        if di_tempat:
            out.append(raw_b("]"))
        fungsi_objek()
        if di_tempat:
            out.append(raw_b("#kolom["))
    sebelum = None
    rumus_sebelum = False
    pertama = True
    di_referensi = False
    stat = {"bagian": [], "gambar": 0, "tabel": 0, "label": []}

    def label_dan_isi(ket, cadangan):
        if not ket:
            return cadangan, []
        m = RE_KETERANGAN.match(ket["teks"].strip()) or RE_KET_LEMAH.match(ket["teks"].strip())
        label = re.sub(r"\s+", " ", m.group(1).replace("\xa0", " "))     # FIX-LABEL-NBSP
        label = label[:-1] + "." if label.endswith(":") else label
        label = label if label.endswith(".") else label + "."           # FIX-KET-LEMAH
        label = re.sub(r"^Fig\.?\s+", "Figure ", label, flags=re.I)      # FIX-FIG-FIGURE
        # FIX-KET-REGULER: isi keterangan tidak tebal (hanya label "Table 1." yang tebal)
        isi_ket = unwrap(drop_prefix(unwrap(ket["inl"], "Strong"), len(m.group(0))), "Strong")
        # FIX-KET-REGULER-ISI: caption yang seluruhnya dimiringkan di Word dibuka bungkus Emph-nya
        # agar tidak menjadi #emph[...] untuk seluruh isi (template tetap menegakkannya).
        if only_wrapped(isi_ket, "Emph"):
            isi_ket = unwrap(isi_ket, "Emph")
        return label, isi_ket

    kosakata = kosakata_naskah(items)                 # FIX-SUBSUB-KALIMAT
    for idx_it, it in enumerate(items):
        j = it["jenis"]
        berikut = items[idx_it + 1]["jenis"] if idx_it + 1 < len(items) else None
        if j == "terpakai":
            continue
        if j == "bagian":
            # FIX-JUDUL-BERURUTAN: dua judul berturutan (level apa pun) TANPA celah pemisah.
            if not pertama and sebelum not in ("bagian", "sub", "subsub", "subsubsub"):
                out.append(raw_b("#celah()"))
            pertama = False
            di_referensi = it["teks"] in BAGIAN_REFERENSI
            stat["bagian"].append(it["teks"])
            out.append({"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": it["teks"]}]]})
        elif j == "sub":
            # FIX-JUDUL-BERURUTAN: judul level 2 sesudah judul level 1/2 tanpa celah.
            if sebelum not in ("bagian", "sub"):
                out.append(raw_b("#celah()"))
            out.append({"t": "Header", "c": [2, ["", [], []], buang_nomor_sub(it["inl"])]})
        elif j == "subsub":
            # FIX-JUDUL-BERURUTAN: judul level 3 sesudah judul level 1-3 tanpa celah.
            if sebelum not in ("bagian", "sub", "subsub"):
                out.append(raw_b("#celah()"))
            out.append({"t": "Header", "c": [3, ["", [], []],
                                             kalimat_subsub(buang_nomor_sub(it["inl"]), kosakata)]})
        elif j == "subsubsub":
            # FIX-HEADING-L4: judul level 4 sesudah judul level 1-4 tanpa celah.
            if sebelum not in ("bagian", "sub", "subsub", "subsubsub"):
                out.append(raw_b("#celah()"))
            out.append({"t": "Header", "c": [4, ["", [], []],
                                             kalimat_subsub(buang_nomor_sub(it["inl"]), kosakata)]})
        elif j == "gambar":
            stat["gambar"] += 1
            stat["gambar_sumber"] = stat.get("gambar_sumber", 0) + sum(len(r) for r in it["baris"])
            if sum(len(r) for r in it["baris"]) > 1 or it.get("label_baris"):   # FIX-GAMBAR-GABUNG
                slot = 442.2 if it["lebar"] > LEBAR_KOLOM_PT * 1.25 else LEBAR_KOLOM_PT
                it["path"] = susun_gambar(it["baris"], workdir, stat["gambar"],
                                          it.get("label_baris"), slot)
            jalur = os.path.join(workdir, ganti_emf(it["path"]))
            rel = "/" + os.path.relpath(jalur, BASE).replace(os.sep, "/")
            label, isi = label_dan_isi(it.get("ket"), "")
            if not it.get("ket"):
                # FIX-GAMBAR-TANPA-KET: gambar tanpa keterangan (mis. rumus yang ditempel sebagai
                # gambar) ditata tanpa nomor "Figure N." -> peringatan, bukan masalah
                laporan["peringatan"].append("Gambar ke-{} tanpa keterangan: ditata tanpa nomor "
                                             "(periksa apakah memang bukan Figure)".format(stat["gambar"]))
            stat["label"].append(label)
            penuh_g = it["lebar"] > LEBAR_KOLOM_PT * 1.25
            argdt = "di-tempat: true, " if di_tempat else ""   # FIX-KOLOM-DI-TEMPAT
            if penuh_g:
                # FIX-GAMBAR-TINGGI-SEMPIT: gambar lebar di Word yang (setelah dibatasi BATAS-TINGGI)
                # tampil tidak lebih lebar dari satu kolom -> ditata satu kolom, bukan selebar
                # halaman dengan ruang kosong di kiri-kanannya
                try:
                    from PIL import Image
                    with Image.open(jalur) as im_g:
                        wpx, hpx = im_g.size
                    if 442.2 * hpx / float(wpx) > batas_tinggi() and \
                            batas_tinggi() * wpx / float(hpx) <= LEBAR_KOLOM_PT * 1.25:
                        penuh_g = False
                        argdt = "penuh: false, " + argdt
                except Exception:
                    pass
            lebar_chart = CHART_LEBAR.get(os.path.basename(it["path"]))   # FIX-UKURAN-CHART
            if lebar_chart:
                penuh_g = lebar_chart > LEBAR_KOLOM_PT
                argdt = "lebar-tampil: {:.1f}pt, penuh: {}, {}".format(
                    lebar_chart, "true" if penuh_g else "false",
                    "di-tempat: true, " if di_tempat else "")
            cat_g = []
            for n_c, c_inl in enumerate(it.get("catatan_teks", [])):    # FIX-CATATAN-OBJEK
                cat_g += ([raw_i("#parbreak()")] if n_c else []) + c_inl
            blok_g = plain([raw_i('#gambar({}, lebar-word: {:.1f}pt, {}label: [{}], isi: ['.format(
                ke_typst_str(rel), it["lebar"], argdt, label))] + isi
                + ([raw_i("], catatan: [")] + cat_g if cat_g else []) + [raw_i("])")])
            if penuh_g:
                putus_kolom(lambda: out.append(blok_g))
            else:
                out.append(blok_g)
        elif j == "tabel":
            stat["tabel"] += 1
            i_word = stat.get("tabel_pandoc", 0)                # FIX-GARIS-TABEL: indeks tabel docx
            stat["tabel_pandoc"] = stat.get("tabel_pandoc", 0) + 1 + len(it.get("panel", []))
            tb = it["blok"]["c"]
            jml_kolom = len(tb[2])
            baris = sum(len(body[3]) for body in tb[4]) + len(tb[3][1])
            for pn in it.get("panel", []):                  # FIX-TABEL-PANEL
                baris += sum(len(body[3]) for body in pn["blok"]["c"][4]) + len(pn["blok"]["c"][3][1]) + 1
            label, isi = label_dan_isi(it.get("ket"), "Table {}.".format(stat["tabel"]))
            if not it.get("ket"):
                # FIX-TABEL-TANPA-KET: caption memang tidak ada di naskah Word (bukan gagal
                # ekstraksi). Nomor "Table N." tetap tampil; QA cukup memberi peringatan.
                laporan["peringatan"].append(
                    "Tabel ke-{} tanpa keterangan di naskah: nomor tabel tetap ditampilkan, "
                    "keterangan perlu dilengkapi penulis.".format(stat["tabel"]))
            stat["label"].append(label)
            # FIX-TABEL-TERPOTONG: di mode di-tempat semua tabel memakai #tabel (terpotong di tempat)
            fungsi = "tabel-panjang" if (baris > BARIS_TABEL_PANJANG and not di_tempat) else "tabel"
            # FIX-LEBAR-TABEL: penuh bila lebar tabel di Word > 1,25 x kolom (aturan gambar Scribus)
            fraksi = [c[1].get("c") for c in tb[2] if c[1].get("t") == "ColWidth"]
            if len(fraksi) == jml_kolom:
                lebar_tb = sum(fraksi) * LEBAR_TEKS_WORD[0]
                for pn in it.get("panel", []):              # FIX-TABEL-PANEL: panel terlebar
                    fr_p = [c[1].get("c") for c in pn["blok"]["c"][2] if c[1].get("t") == "ColWidth"]
                    lebar_tb = max(lebar_tb, sum(fr_p) * LEBAR_TEKS_WORD[0])
                    jml_kolom = max(jml_kolom, len(pn["blok"]["c"][2]))
                arg_penuh = "penuh: {}, ".format("true" if lebar_tb > LEBAR_KOLOM_PT * 1.25 else "false")
            else:
                arg_penuh = ""          # lebar tidak diketahui -> aturan jumlah kolom lama
            # FIX-TABEL-ISI: lebar tabel menurut ISI-nya (bukan lebar Word): muat satu kolom bila lebar
            # alaminya <= kolom, atau bila hanya sedikit lebih lebar dan teks panjang di sel boleh dipecah
            # menjadi 2+ baris (lebar minimum = kata terpanjang tiap kolom muat di kolom)
            # FIX-TABEL-SOFT-HYPHEN: soft hyphen HANYA untuk blok Table (bukan Chart/paragraf).
            if it["blok"].get("t") == "Table":
                _soft_hyphen_ast(it["blok"])
            for pn in it.get("panel", []):
                if pn["blok"].get("t") == "Table":
                    _soft_hyphen_ast(pn["blok"])
            ukur_t = lebar_isi_tabel([it["blok"]] + [pn["blok"] for pn in it.get("panel", [])])
            lebar_kol_t = None
            if ukur_t:
                alami, minimum, per_tabel = ukur_t
                # satu kolom bila tiap bagian tabel muat di kolom dengan sel paling banyak 2 baris
                hasil_k = [bagi_lebar_kolom(nat, mn, LEBAR_KOLOM_PT) for nat, mn in per_tabel]
                satu_kolom = all(h is not None and h[1] <= 1.7 for h in hasil_k) and jml_kolom <= 6
                wadah = LEBAR_KOLOM_PT if satu_kolom else 442.2
                if not satu_kolom:
                    hasil_k = [bagi_lebar_kolom(nat, mn, wadah) for nat, mn in per_tabel]
                if all(h is not None for h in hasil_k):
                    lebar_kol_t = [[w / wadah for w in h[0]] for h in hasil_k]
                arg_penuh = "penuh: {}, ".format("false" if satu_kolom else "true")
            arg = "" if fungsi == "tabel-panjang" else "jumlah-kolom: {}, {}".format(jml_kolom, arg_penuh)
            arg += "kepala: {}, ".format(max(1, len(tb[3][1])))      # FIX-KEPALA-TEBAL
            if i_word < len(GARIS_TABEL) and not GARIS_TABEL[i_word]:
                arg += "garis-baris: false, "             # FIX-GARIS-TABEL: tanpa garis antarbaris
            penuh_t = fungsi == "tabel-panjang" or "penuh: true" in arg or \
                (arg_penuh == "" and jml_kolom > 3)
            if di_tempat:   # FIX-KOLOM-DI-TEMPAT: objek satu kolom juga di tempat
                arg += "di-tempat: true, "
            def tulis_tabel():
                out.append(plain([raw_i("#{}({}label: [{}], isi: [".format(fungsi, arg, label))]
                                 + isi + [raw_i("])[")]))
                # FIX-TABEL-PENUH: proporsi kolom dari Word dinormalkan ke 100% tempatnya
                for bagian_t in [it] + it.get("panel", []):
                    spek = bagian_t["blok"]["c"][2]
                    # FIX-TABEL-ISI: lebar kolom mengikuti isi (dihitung bagi_lebar_kolom); bila tak
                    # terhitung, auto (Typst)
                    i_bag = ([it] + it.get("panel", [])).index(bagian_t)
                    fr_isi = lebar_kol_t[i_bag] if lebar_kol_t and i_bag < len(lebar_kol_t) else None
                    for c in spek:
                        c[1] = {"t": "ColWidthDefault"}
                    # pandoc menormalkan persentase lebar kolom ke 100% -> lebar pt ditulis sesudah
                    # konversi (lihat FIX-TABEL-PT di main)
                    stat.setdefault("lebar_tabel", []).append(
                        [round(f * (LEBAR_KOLOM_PT if "penuh: false" in arg else 442.2), 1) for f in fr_isi]
                        if fr_isi and len(fr_isi) == len(spek) else None)
                    if bagian_t.get("judul_panel"):          # FIX-TABEL-PANEL: judul panel
                        if bagian_t is not it:
                            out.append(raw_b("#v(6pt)"))
                        out.append(plain([raw_i("#block(below: 4pt, strong[")]
                                         + unwrap(bagian_t["judul_panel"], "Strong") + [raw_i("])")]))
                    out.append(bagian_t["blok"])
                if it.get("catatan_teks"):                       # FIX-CATATAN-OBJEK
                    isi_c = []
                    for n_c, c_inl in enumerate(it["catatan_teks"]):
                        isi_c += ([raw_i("#parbreak()")] if n_c else []) + c_inl
                    out.append(plain([raw_i("#catatan-objek[")] + isi_c + [raw_i("]")]))
                for c_path in it.get("catatan", []):            # FIX-GAMBAR-GABUNG: catatan tabel
                    c_rel = "/" + os.path.relpath(os.path.join(workdir, ganti_emf(c_path)), BASE).replace(os.sep, "/")
                    out.append(raw_b('#v(4pt)\n#image({}, width: 100%)'.format(ke_typst_str(c_rel))))
                    stat["gambar_sumber"] = stat.get("gambar_sumber", 0) + 1
                out.append(raw_b("]"))
            if penuh_t:
                putus_kolom(tulis_tabel)
            else:
                tulis_tabel()
        elif j == "daftar":
            b = it["blok"]
            # FIX-CELAH-DAFTAR: beri jarak 11 pt sebelum daftar, kecuali daftar langsung
            # mengikuti judul (semua level, penomoran a./b. = lanjutan judul) atau melanjutkan
            # daftar sebelumnya.
            if sebelum not in ("sub", "subsub", "subsubsub", "daftar"):
                out.append(raw_b("#celah()"))
            # FIX-BUTIR-REKAT: daftar sesudah judul sub/subsub (atau lanjutan daftar) yang
            # diikuti paragraf isi tidak boleh terpisah dari paragraf itu ke kolom berikutnya.
            if b["t"] == "OrderedList":
                mulai, gaya = b["c"][0][0], b["c"][0][1]["t"]
                daftar = b["c"][1]
            else:
                mulai, gaya, daftar = 1, None, b["c"]
            # FIX-BUTIR-REKAT-BARIS: daftar satu item / item terakhir yang pendek (≈1 baris)
            # diikuti paragraf -> sticky agar tidak terdampar sendirian di dasar kolom.
            teks_item = [stringify(gabung_inline_item(x)).strip() for x in daftar]
            pendek = bool(teks_item) and len(teks_item[-1]) <= 60
            rekat = berikut == "para" and (
                sebelum in ("sub", "subsub", "subsubsub", "daftar")
                or len(daftar) == 1 or pendek)
            for n, item in enumerate(daftar):
                tanda = penanda_daftar(gaya, mulai + n) if gaya else "•"
                rekat_item = rekat and n == len(daftar) - 1
                out.append(plain([raw_i("#butir([{}]{})[".format(
                    tanda, ", rekat: true" if rekat_item else ""))]
                                 + gabung_inline_item(item) + [raw_i("]")]))
        elif j == "wawancara":
            out.append(plain([raw_i("#wawancara[")] + it["inl"] + [raw_i("]")]))
        elif j == "berlabel":     # FIX-LABEL-GANTUNG
            out.append(plain([raw_i("#label-gantung([{}])[".format(it["label"]))] + it["inl_isi"] + [raw_i("]")]))
        elif j == "rumus_teks":                              # FIX-RUMUS-TEKS
            out.append(plain([raw_i("#rumus-teks[")] + it["inl"] + [raw_i("]")]))
        elif j == "rumus":                                   # FIX-RUMUS-BERNOMOR
            m_ = {"t": "Math", "c": [{"t": "DisplayMath"}, it["tex"]]}
            if it["nomor"]:
                out.append({"t": "Para", "c": [raw_i('#rumus-nomor("({})")['.format(it["nomor"])), m_,
                                                raw_i("]")]})
            else:
                out.append({"t": "Para", "c": [m_]})
        elif j == "para":
            if di_referensi:
                bagian_ref = pecah_entri_referensi(it["inl"])          # FIX-REF-SEPARAGRAF
                if len(bagian_ref) > 1:
                    stat["ref_dipisah"] = stat.get("ref_dipisah", 0) + len(bagian_ref) - 1
                for bag in bagian_ref:
                    out.append(plain([raw_i("#referensi[")] + bag + [raw_i("]")]))
            else:
                if it.get("calon_sub"):     # FIX-QA-SUB-POLOS
                    laporan["masalah"].append(
                        "Kemungkinan sub-bab tanpa format: \"{}\" (tebalkan atau beri nomor, mis. "
                        "2.1, di naskah)".format(stringify(it["inl"]).strip()[:60]))
                # FIX-INDENT-LANJUTAN: paragraf sesudah kutipan/daftar/rumus yang di Word tidak
                # ber-indent = lanjutan paragraf sebelumnya -> tanpa indent baris pertama
                if (sebelum in ("wawancara", "daftar", "berlabel") or rumus_sebelum) and \
                        kunci_paragraf(stringify(it["inl"])) in TANPA_INDENT and it["blok"].get("t") == "Para":
                    it["blok"]["c"] = [raw_i("#h(-25pt)")] + it["blok"]["c"]
                out.append(it["blok"])
        else:
            out.append(it["blok"])
        rumus_sebelum = j in ("rumus", "rumus_teks") or j == "para" and bool(it.get("inl")) and all(
            x.get("t") in ("Math", "Space", "SoftBreak") for x in it["inl"]) and any(
            x.get("t") == "Math" and x["c"][0].get("t") == "DisplayMath" for x in it["inl"])
        sebelum = j
    if di_tempat:
        out.append(raw_b("]"))
    if stat.get("ref_dipisah"):                       # FIX-REF-SEPARAGRAF
        laporan["peringatan"].append("Referensi: {} entri yang diketik bersambung dalam satu paragraf "
                                     "dipisah menjadi entri tersendiri".format(stat["ref_dipisah"]))
    # FIX-PENANDA: penanda posisi nol-lebar #pm(n); di awal setiap paragraf/judul/butir/
    # referensi (mode di-tempat). Dipakai untuk identitas paragraf, keputusan objek, dan QA
    # urutan. peta_objek: (nomor objek, penanda terakhir sebelum objek, penanda sesudahnya).
    stat["peta_objek"] = []
    if di_tempat:
        n_pm, tunggu, n_obj = 0, [], 0
        for blk in out:
            t = blk.get("t")
            tanda = None
            if t == "Header":
                n_pm += 1
                blk["c"][2] = [raw_i("#pm({});".format(n_pm))] + blk["c"][2]
                tanda = n_pm
            elif t == "Para":
                n_pm += 1
                blk["c"] = [raw_i("#pm({});".format(n_pm))] + blk["c"]
                tanda = n_pm
            elif t == "Plain" and blk["c"] and blk["c"][0].get("t") == "RawInline":
                kepala = blk["c"][0]["c"][1]
                if kepala.startswith(("#butir(", "#referensi[", "#wawancara[", "#label-gantung(")):
                    n_pm += 1
                    blk["c"] = [blk["c"][0], raw_i("#pm({});".format(n_pm))] + blk["c"][1:]
                    tanda = n_pm
                elif re.match(r"#(gambar|tabel)\(", kepala):
                    n_obj += 1
                    tunggu.append([n_obj, n_pm, None])
            if tanda is not None:
                if t == "Header":
                    blk["c"][2].append(raw_i("#pe({})".format(tanda)))
                elif t == "Para":
                    blk["c"].append(raw_i("#pe({})".format(tanda)))
                else:
                    blk["c"].insert(len(blk["c"]) - 1, raw_i("#pe({})".format(tanda)))
                for o in tunggu:
                    if o[2] is None:
                        o[2] = tanda
                stat["peta_objek"] += [tuple(o) for o in tunggu if o[2] is not None]
                tunggu = [o for o in tunggu if o[2] is None]
        stat["peta_objek"] += [tuple(o) for o in tunggu]
    return out, stat


# ======================================================================
#  QA otomatis
# ======================================================================
def sesuaikan_chart_skala(body_path):
    """FIX-QA-CHART: skala objek #gambar (mis. 0.800 dari optimasi ukuran) ikut memperkecil
    teks chart. Terapkan sekali ke CHART_EFEKTIF agar peringatan QA memakai ukuran nyata."""
    try:
        with open(body_path, encoding="utf-8") as f:
            isi = f.read()
    except OSError:
        return
    for m in re.finditer(r'#gambar\("([^"]*chart_render_[^"]+)"[^\n]*?\bskala: ([\d.]+)', isi):
        nama = os.path.basename(m.group(1))
        if nama in CHART_EFEKTIF and nama not in CHART_EFEKTIF_SKALA:
            CHART_EFEKTIF[nama] *= float(m.group(2))
            CHART_EFEKTIF_SKALA.add(nama)


def qa(pdf, meta, stat, laporan, teks_sumber, peringatan_typst):
    hasil = {"status": "LULUS", "masalah": list(laporan["masalah"]),
             "peringatan": list(laporan["peringatan"]) + peringatan_typst}
    if laporan.get("normalisasi"):                       # FIX-NORMALISASI: jejak perubahan naskah
        hasil["normalisasi"] = laporan["normalisasi"]
    if laporan.get("rapor_naskah"):                      # FIX-PROFIL-IDEAL
        hasil["rapor_naskah"] = laporan["rapor_naskah"]
    # FIX-QA-CHART: chart yang teksnya menjadi terlalu kecil setelah dibatasi lebar halaman
    # (mis. chart asli berfont kecil pada kanvas lebar) -> peringatan, bukan penahan.
    for nama, pt in sorted(CHART_EFEKTIF.items()):
        if pt < 8.0:
            hasil["peringatan"].append(
                "Chart {}: ukuran teks efektif {:.1f} pt (< 8 pt); pertimbangkan memperbesar "
                "chart atau menyederhanakan label".format(nama, pt))

    for kunci in ("judul", "abstrak", "email"):
        if not str(meta.get(kunci, "")).strip():
            hasil["masalah"].append("Metadata kosong: " + kunci)
    if not meta.get("penulis"):
        hasil["masalah"].append("Metadata kosong: penulis")
    if len(meta.get("kata_kunci", [])) < 3:
        hasil["masalah"].append("Kata kunci kurang dari 3")
    # GANTI MENJADI:
    for k in ("diterima", "disetujui", "terbit"):
      if not meta.get("riwayat", {}).get(k):
        hasil["peringatan"].append("Riwayat artikel kosong: " + k)
    for grup in BAGIAN_WAJIB:
        if not any(any(b == g or b.startswith(g + " ") for g in grup) for b in stat["bagian"]):
            hasil["masalah"].append("Bagian wajib tidak ditemukan: " + grup[0])
    if any("unknown font" in p for p in peringatan_typst):
        hasil["masalah"].append("Font tidak ditemukan (lihat peringatan Typst)")
    if any("did not converge" in p for p in peringatan_typst):   # FIX-QA-KONVERGEN
        hasil["masalah"].append("Tata letak Typst tidak konvergen (posisi bisa tidak konsisten)")

    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # PyMuPDF lama
        except ImportError:
            hasil["masalah"].append("PyMuPDF tidak terpasang: pemeriksaan isi PDF tidak bisa dijalankan")
            fitz = None
    if fitz:
        with fitz.open(pdf) as doc:          # FIX-MEMORI-FITZ
            hasil["halaman"] = doc.page_count
            semua = []
            for i, pg in enumerate(doc):
                t = pg.get_text()
                semua.append(t)
                isi_bersih = re.sub(r"\s+", " ", t)
                if re.search(r"<\s*/?\s*(?:table|thead|tbody|tr|td|th)\b|#table\s*\(|table\.cell\s*\(", isi_bersih, re.I):
                    hasil["masalah"].append("Markup tabel mentah tercetak di halaman {}".format(i + 1))
                # buang header/footer berjalan
                if i > 0 and len(isi_bersih) < 200 and not pg.get_images():
                    hasil["masalah"].append("Halaman {} (hampir) kosong".format(i + 1))
                for img in pg.get_image_info():
                    w_pt = img["bbox"][2] - img["bbox"][0]
                    if w_pt > 30 and img.get("width"):
                        dpi = img["width"] / (w_pt / 72.0)
                        if dpi < 150:
                            hasil["peringatan"].append(
                                "Gambar beresolusi rendah ({:.0f} dpi) di halaman {}".format(dpi, i + 1))
            # FIX-HAL-AKHIR-TIPIS: halaman terakhir yang hanya berisi beberapa baris
            if doc.page_count > 1:
                akhir = doc[doc.page_count - 1]
                ys = {round(l["spans"][0]["origin"][1]) + (1000 if l["bbox"][0] >= 290 else 0)
                      for bl in akhir.get_text("dict")["blocks"] for l in bl.get("lines", [])
                      if l["spans"] and 100 < l["bbox"][1] < 765 and "".join(x["text"] for x in l["spans"]).strip()}
                if len(ys) < 12:
                    hasil["masalah"].append("Halaman terakhir hanya berisi {} baris".format(len(ys)))
        for h_k, k_k in halaman_kosong_bawah(pdf):        # FIX-RUANG-KOSONG
            hasil["masalah"].append("Halaman {} kosong {:.0f} pt di bagian bawah".format(h_k, k_k))
        for h_l, n_l in baris_luber(pdf):                # FIX-TEKS-LUBER
            hasil["masalah"].append("{} baris teks melewati dasar kolom di halaman {}".format(n_l, h_l))
        # FIX-QA-SOFT-HYPHEN: soft hyphen tidak terlihat di PDF; jangan dianggap teks hilang.
        pdf_teks = re.sub(r"\s+", " ", " ".join(semua)).replace("\xad", "")
        # FIX-QA-HURUF: judul bagian di PDF ditulis KAPITAL; bandingkan tanpa membedakan huruf.
        kata_pdf = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", pdf_teks)}
        kata_src = re.findall(r"[A-Za-z]{4,}", teks_sumber)
        if kata_src:
            hilang = [w for w in kata_src if w.lower() not in kata_pdf]
            cakupan = 1 - len(hilang) / float(len(kata_src))
            hasil["cakupan_teks"] = round(cakupan, 4)
            if cakupan < 0.995:
                hasil["masalah"].append("Teks hilang di PDF: cakupan {:.2%}, contoh: {}".format(
                    cakupan, ", ".join(sorted(set(hilang))[:8])))
        for lbl in stat["label"]:
            if lbl not in pdf_teks:
                hasil["masalah"].append("Label tidak ditemukan di PDF: " + lbl)
    if hasil["masalah"]:
        hasil["status"] = "DITAHAN"
    return hasil


# ======================================================================
#  Utama
# ======================================================================
def jalankan_soffice(berkas, outdir):
    """FIX-SOFFICE-PROFIL: LibreOffice dengan profil pengguna sendiri per proses build (dua build
    bersamaan dengan profil yang sama saling mengunci dan konversinya gagal); dicoba ulang sekali."""
    import tempfile
    profil = os.path.join(tempfile.gettempdir(), "lo_profil_{}".format(os.getpid()))
    os.makedirs(profil, exist_ok=True)
    # FIX-SOFFICE-FONT: HOME profil sementara membuat font pengguna (~/.fonts) tidak terlihat
    # oleh LibreOffice, sehingga teks chart jatuh ke font pengganti (mis. LiberationSerif).
    # Tautkan direktori font pengguna ke profil agar Times New Roman (dan font naskah lain)
    # tetap dipakai saat chart dirender.
    home_asli = os.path.expanduser("~")
    for nama_font in (".fonts", os.path.join(".local", "share", "fonts")):
        sumber = os.path.join(home_asli, nama_font)
        tujuan = os.path.join(profil, nama_font)
        if os.path.isdir(sumber) and not os.path.exists(tujuan):
            try:
                os.makedirs(os.path.dirname(tujuan), exist_ok=True)
                os.symlink(sumber, tujuan)
            except OSError:
                pass
    url = "file://" + ("/" if not profil.startswith("/") else "") + profil.replace(os.sep, "/")
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(berkas))[0] + ".pdf")
    # FIX-SOFFICE-PYTHONHOME: LibreOffice membawa Python sendiri. PYTHONHOME/PYTHONPATH dari
    # lingkungan build (Anaconda/venv) membuat "Could not find platform independent libraries".
    # Bersihkan variabel Python dan arahkan HOME/TEMP ke profil LibreOffice yang writable.
    env = os.environ.copy()
    for var in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONEXECUTABLE"):
        env.pop(var, None)
    env["HOME"] = profil
    env["USERPROFILE"] = profil
    env["TMP"] = tempfile.gettempdir()
    env["TEMP"] = tempfile.gettempdir()
    env["PYTHONNOUSERSITE"] = "1"
    env["SAL_USE_VCLPLUGIN"] = "svp"
    for _ in range(2):
        rc, out, err = jalankan([SOFFICE, "-env:UserInstallation=" + url, "--headless",
                                 "--convert-to", "pdf", "--outdir", outdir, berkas], env=env)
        if rc == 0 and os.path.isfile(pdf):
            break
    return rc, out, err


def potong_gambar_word(docx_in, workdir, laporan):
    """FIX-CROP-WORD: gambar yang dipotong (crop) di Word (<a:srcRect l= t= r= b=>) dibaca pandoc
    sebagai gambar utuh sehingga muncul ruang kosong/bagian yang disembunyikan penulis. Tiap
    gambar ber-crop dipotong dengan PIL ke berkas media baru dan srcRect dibuang."""
    import zipfile
    import io
    with zipfile.ZipFile(docx_in) as z:
        isi = {n: z.read(n) for n in z.namelist()}
    doc = isi["word/document.xml"].decode("utf-8")
    pola = re.compile(r'(<a:blip\b[^>]*?r:embed="([^"]+)"[^>]*?(?:/>|>(?:(?!<a:blip\b).)*?</a:blip>))\s*'
                      r'<a:srcRect\b([^>]*)/>', flags=re.S)
    if not pola.search(doc):
        return docx_in
    rels = isi["word/_rels/document.xml.rels"].decode("utf-8")
    target = dict(re.findall(r'<Relationship [^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels))
    target.update({a: b for b, a in re.findall(r'<Relationship [^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
    from PIL import Image
    n = [0]

    def ganti(m):
        blip, rid, attr = m.group(1), m.group(2), m.group(3)
        f = dict((k, int(v)) for k, v in re.findall(r'\b([ltrb])="(-?\d+)"', attr))
        l, t, r, b = (max(0, f.get(k, 0)) / 100000.0 for k in "ltrb")
        nama = target.get(rid)
        if not nama or l + r >= 0.98 or t + b >= 0.98 or (l + t + r + b) < 0.002:
            return blip
        jalur = "word/" + nama.lstrip("/") if not nama.startswith("word/") else nama
        if jalur not in isi:
            return blip
        try:
            im = Image.open(io.BytesIO(isi[jalur]))
            im.load()
            W, H = im.size
            kotak = (int(round(W * l)), int(round(H * t)), int(round(W * (1 - r))), int(round(H * (1 - b))))
            hasil = im.crop(kotak)
            if hasil.mode not in ("RGB", "RGBA", "L"):
                hasil = hasil.convert("RGBA")
            buf = io.BytesIO()
            hasil.save(buf, "PNG")
        except Exception as e:
            laporan["peringatan"].append("Crop gambar {} gagal: {}".format(nama, e))
            return blip
        n[0] += 1
        baru = "media/crop_{}.png".format(n[0])
        isi["word/" + baru] = buf.getvalue()
        rid_baru = "rIdCrop{}".format(n[0])
        rels_tambah.append('<Relationship Id="{}" Type="http://schemas.openxmlformats.org/officeDocument/'
                           '2006/relationships/image" Target="{}"/>'.format(rid_baru, baru))
        return blip.replace('r:embed="{}"'.format(rid), 'r:embed="{}"'.format(rid_baru))
    rels_tambah = []
    doc = pola.sub(ganti, doc)
    if not n[0]:
        return docx_in
    rels = rels.replace("</Relationships>", "".join(rels_tambah) + "</Relationships>")
    ctypes = isi["[Content_Types].xml"].decode("utf-8")
    if 'Extension="png"' not in ctypes:
        ctypes = ctypes.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
    isi["word/document.xml"] = doc.encode("utf-8")
    isi["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    isi["[Content_Types].xml"] = ctypes.encode("utf-8")
    keluar = os.path.join(workdir, "_naskah_crop.docx")
    with zipfile.ZipFile(keluar, "w", zipfile.ZIP_DEFLATED) as z:
        for nama, data in isi.items():
            z.writestr(nama, data)
    laporan["peringatan"].append("{} gambar dipotong sesuai crop Word".format(n[0]))
    return keluar


def elemen_body(body):
    """FIX-BENTUK-WORD: rentang (awal, akhir, jenis) elemen tingkat atas <w:body> (paragraf, tabel,
    lainnya). Paragraf di dalam textbox/tabel tidak dihitung sebagai elemen tingkat atas."""
    hasil, i, n = [], 0, len(body)
    pola = re.compile(r"<(/?)w:(p|tbl|sdt)([ >/])")
    while i < n:
        m = pola.search(body, i)
        if not m:
            break
        if m.group(1):                                   # penutup liar
            i = m.end()
            continue
        jenis, awal = m.group(2), m.start()
        if body[m.start():m.start() + 200].split(">", 1)[0].endswith("/"):
            hasil.append((awal, body.index(">", awal) + 1, jenis))
            i = body.index(">", awal) + 1
            continue
        dalam, j = 0, awal
        buka = re.compile(r"<(/?)w:{}([ >/])".format(jenis))
        while True:
            mm = buka.search(body, j)
            if not mm:
                j = n
                break
            tag_akhir = body.index(">", mm.start())
            swa = body[tag_akhir - 1] == "/"
            if mm.group(1):
                dalam -= 1
            elif not swa:
                dalam += 1
            j = tag_akhir + 1
            if dalam == 0:
                break
        hasil.append((awal, j, jenis))
        i = j
    return hasil


def teks_tampak(xml):
    """Teks paragraf di luar gambar/textbox (mc:AlternateContent, txbxContent, pict)."""
    x = re.sub(r"<mc:AlternateContent>.*?</mc:AlternateContent>|<w:txbxContent>.*?</w:txbxContent>"
               r"|<w:pict>.*?</w:pict>", "", xml, flags=re.S)
    return "".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", x))


def render_bentuk_docx(docx_in, workdir, laporan):
    """FIX-BENTUK-WORD: diagram yang disusun dari shape/textbox Word (wps:wsp, wpg, VML) dan
    dijangkarkan ke paragraf-paragraf KOSONG tidak dibaca pandoc. Tiap kelompok paragraf kosong
    berisi shape (boleh diselingi paragraf kosong, maks. 6) dirender LibreOffice sebagai satu
    gambar (dipangkas ke isi gambarnya) dan menggantikan paragraf-paragraf itu."""
    import zipfile
    with zipfile.ZipFile(docx_in) as z:
        isi = {nm: z.read(nm) for nm in z.namelist()}
    doc = isi["word/document.xml"].decode("utf-8")
    if not re.search(r"<wps:wsp\b|<wpg:wgp\b|<v:shape\b|<v:group\b", doc):
        return docx_in, 0
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    a_body, z_body = doc.index("<w:body>") + len("<w:body>"), doc.index("</w:body>")
    body = doc[a_body:z_body]
    sect = re.search(r"<w:sectPr[ >].*?</w:sectPr>", body, flags=re.S)
    sect = re.sub(r"<w:(?:header|footer)Reference\b[^>]*/>|<w:titlePg\s*/>", "", sect.group(0) if sect else "")
    el = elemen_body(body)

    def bentuk(x):
        # FIX-GRUP-GAMBAR: grup Word berisi potongan gambar (+ shape/teks) juga dirender jadi satu gambar
        utama = re.sub(r"<mc:Fallback>.*?</mc:Fallback>", "", x, flags=re.S)
        return bool(re.search(r"<wps:wsp\b|<wpg:wgp\b|<v:shape\b|<v:group\b|<v:line\b|<v:oval\b|<v:rect\b", x)) \
            and ("pic:pic" not in utama or bool(re.search(r"<wpg:wgp\b.*?<pic:pic\b", utama, flags=re.S)))
    kosong = [e[2] == "p" and not teks_tampak(body[e[0]:e[1]]).strip() for e in el]
    ada = [e[2] == "p" and kosong[k] and bentuk(body[e[0]:e[1]]) for k, e in enumerate(el)]
    kelompok, k = [], 0
    while k < len(el):
        if not ada[k]:
            k += 1
            continue
        a, b, j = k, k, k + 1
        while j < len(el) and kosong[j] and j - b <= 6:
            if ada[j]:
                b = j
            j += 1
        kelompok.append((a, b))
        k = b + 1
    if not kelompok:
        return docx_in, 0
    tmp = os.path.join(workdir, "_bentuk")
    os.makedirs(tmp, exist_ok=True)
    rels = isi["word/_rels/document.xml.rels"].decode("utf-8")
    ganti, n = [], 0
    for g, (a, b) in enumerate(kelompok, 1):
        potong = body[el[a][0]:el[b][1]]
        mini = os.path.join(tmp, "bentuk{}.docx".format(g))
        with zipfile.ZipFile(mini, "w", zipfile.ZIP_DEFLATED) as z:
            for nm, data in isi.items():
                if nm == "word/document.xml":
                    data = (doc[:a_body] + potong + sect + doc[z_body:]).encode("utf-8")
                z.writestr(nm, data)
        rc, out, err = jalankan_soffice(mini, tmp)
        pdf = mini[:-5] + ".pdf"
        if rc != 0 or not os.path.isfile(pdf):
            laporan["masalah"].append("Diagram shape ke-{} gagal dirender LibreOffice ({}): {}".format(
                g, SOFFICE, (err or out or "kode {}".format(rc)).strip()[-200:]))
            continue
        with fitz.open(pdf) as pdf_doc:      # FIX-MEMORI-FITZ
            hal = pdf_doc[0]
            kotak = None
            for d in hal.get_drawings():
                kotak = d["rect"] if kotak is None else kotak | d["rect"]
            for blk in hal.get_text("dict")["blocks"]:
                kotak = fitz.Rect(blk["bbox"]) if kotak is None else kotak | fitz.Rect(blk["bbox"])
            if kotak is None or kotak.is_empty or kotak.width < 20 or kotak.height < 20:
                continue
            nama_png = "bentuk_render_{}.png".format(g)
            hal.get_pixmap(dpi=300, clip=kotak + (-2, -2, 2, 2)).save(os.path.join(tmp, nama_png))
        with open(os.path.join(tmp, nama_png), "rb") as f:
            isi["word/media/" + nama_png] = f.read()
        rid = "rIdBentuk{}".format(g)
        rels = rels.replace("</Relationships>",
            '<Relationship Id="{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/{}"/></Relationships>'.format(rid, nama_png))
        cx, cy = int((kotak.width + 4) * 12700), int((kotak.height + 4) * 12700)
        par = ('<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
               '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
               '<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{id}" name="{n}"/>'
               '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
               '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
               '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
               '<pic:nvPicPr><pic:cNvPr id="0" name="{n}"/><pic:cNvPicPr/></pic:nvPicPr>'
               '<pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{r}"/>'
               '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
               '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
               '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
               '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>').format(
                   cx=cx, cy=cy, id=90000 + g, n=nama_png, r=rid)
        ganti.append((el[a][0], el[b][1], par))
        n += 1
    for p0, p1, par in sorted(ganti, reverse=True):
        body = body[:p0] + par + body[p1:]
    doc = doc[:a_body] + body + doc[z_body:]
    ctypes = isi["[Content_Types].xml"].decode("utf-8")
    if 'Extension="png"' not in ctypes:
        ctypes = ctypes.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
    isi["word/document.xml"] = doc.encode("utf-8")
    isi["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    isi["[Content_Types].xml"] = ctypes.encode("utf-8")
    keluar = os.path.join(workdir, "_naskah_bentuk.docx")
    with zipfile.ZipFile(keluar, "w", zipfile.ZIP_DEFLATED) as z:
        for nm, data in isi.items():
            z.writestr(nm, data)
    if n:
        laporan["peringatan"].append("{} diagram shape/textbox Word dirender sebagai gambar".format(n))
    return keluar, n


def baca_garis_tabel(docx):
    """FIX-GARIS-TABEL: untuk tiap tabel tingkat atas docx -> True bila Word menggambar garis
    antarbaris (insideH pada tabel/gaya tabel, atau border sel pada sebagian besar baris tengah)."""
    import zipfile
    try:
        z = zipfile.ZipFile(docx)
        doc = z.read("word/document.xml").decode("utf-8")
        gaya = z.read("word/styles.xml").decode("utf-8")
    except Exception:
        return []
    def ada_garis(x):
        return bool(re.search(r'<w:insideH w:val="(?!nil|none)', x or ""))
    gaya_h = {m.group(1): ada_garis(m.group(0)) for m in
              re.finditer(r'<w:style [^>]*w:styleId="([^"]+)".*?</w:style>', gaya, flags=re.S)}
    a_body = doc.index("<w:body>") + len("<w:body>")
    body = doc[a_body:doc.index("</w:body>")]
    hasil = []
    for p0, p1, jenis in elemen_body(body):
        if jenis != "tbl":
            continue
        t = body[p0:p1]
        tpr = re.search(r"<w:tblPr>.*?</w:tblPr>", t, flags=re.S)
        tpr = tpr.group(0) if tpr else ""
        st = re.search(r'<w:tblStyle w:val="([^"]+)"', tpr)
        if re.search(r"<w:tblBorders>", tpr):
            garis = ada_garis(tpr)
        else:
            garis = gaya_h.get(st.group(1), False) if st else False
        baris = re.findall(r"<w:tr[ >].*?</w:tr>", t, flags=re.S)
        tengah = baris[2:-1]
        if tengah:
            bergaris = sum(1 for r in tengah if re.search(r'<w:(?:top|bottom) w:val="(?!nil|none)', r))
            if bergaris >= 0.5 * len(tengah):
                garis = True
            elif re.search(r"<w:tcBorders>", "".join(tengah)) and bergaris == 0:
                garis = False
        hasil.append(garis)
    return hasil


GARIS_TABEL = []        # FIX-GARIS-TABEL: diisi di main() dari docx
_FONT_UKUR = {}


def bagi_lebar_kolom(nat, mn, tersedia):
    """FIX-TABEL-ISI: lebar tiap kolom (pt). Muat -> lebar alami; tidak muat -> kolom terlebar
    dipadatkan sampai batas c yang sama (water-filling), tidak di bawah kata terpanjangnya.
    Hasil (lebar, maks. jumlah baris pecahan per sel) atau None bila minimum pun tidak muat."""
    if sum(nat) <= tersedia:
        return list(nat), 1.0
    if sum(mn) > tersedia:
        return None
    lo, hi = 0.0, max(nat)
    for _ in range(50):
        c = (lo + hi) / 2
        if sum(min(n, max(c, m)) for n, m in zip(nat, mn)) > tersedia:
            hi = c
        else:
            lo = c
    lebar = [min(n, max(lo, m)) for n, m in zip(nat, mn)]
    sisa = tersedia - sum(lebar)
    if sisa > 0:                       # sisa dibagi ke kolom yang dipadatkan
        padat = [i for i, (w, n) in enumerate(zip(lebar, nat)) if w < n]
        for i in padat:
            lebar[i] += sisa / len(padat)
    baris = max((n - 8.0) / max(w - 8.0, 1.0) for n, w in zip(nat, lebar))
    return lebar, baris


def _soft_hyphen(t):
    """FIX-TABEL-KATA-PANJANG: sisipkan soft hyphen pada token panjang yang benar-benar
    tak bisa dipecah: camelCase, singkatan bergaya CamelCASE, atau token dengan pemisah
    / . - _ # @. Kata normal (organizations, institutionalization) tidak disentuh."""
    def satu(m):
        w = m.group(0)
        if len(w) <= 12:
            return w
        camel = bool(re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]", w))
        # FIX-TABEL-KATA-PANJANG: pemisah / . - _ # @ hanya untuk token mirip identitas/URL
        # (mengandung digit atau _#@), bukan senyawa biasa seperti "company-linked" atau
        # "jumping/boomerang" yang sudah punya titik patah alami dan tidak boleh menambah baris.
        simbol = bool(re.search(r"[/.\-_#@]", w)) and (
            bool(re.search(r"\d", w)) or bool(re.search(r"[_#@]", w))
            or bool(re.search(r"https?://|www\.|doi\.?", w, re.I)))
        if not (camel or simbol):
            return w
        w = re.sub(r"(?<=[a-z])(?=[A-Z])", "\xad", w)
        w = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "\xad", w)
        w = re.sub(r"(?<=[/.\-_])(?=\S)", "\xad", w)
        return w
    return re.sub(r"\S{13,}", satu, t)


def _soft_hyphen_ast(x):
    """Terapkan _soft_hyphen ke semua token Str pada AST tabel (rekursif)."""
    if isinstance(x, list):
        for y in x:
            _soft_hyphen_ast(y)
    elif isinstance(x, dict):
        if x.get("t") == "Str":
            x["c"] = _soft_hyphen(x["c"])
        for v in x.values():
            _soft_hyphen_ast(v)


def lebar_isi_tabel(tabel_list, ukuran=11.0, inset=11.0):
    """FIX-TABEL-ISI: (lebar alami, lebar minimum) tabel pandoc dalam pt untuk font narasi:
    alami = jumlah lebar teks terpanjang tiap kolom (tanpa pemecahan baris), minimum = jumlah
    lebar kata terpanjang tiap kolom. Sel gabungan (colspan > 1) diabaikan. None bila gagal."""
    try:
        from PIL import ImageFont
        if "f" not in _FONT_UKUR:
            fnt = font_label_panel(100)
            _FONT_UKUR["f"] = fnt if hasattr(fnt, "getlength") else None
        f = _FONT_UKUR["f"]
        if f is None:
            return None
        ukur = lambda t: f.getlength(t) * ukuran / 100.0
        alami_t, minimum_t, kolom_t = 0.0, 0.0, []
        for tb in tabel_list:
            c = tb["c"]
            n = len(c[2])
            nat, mn = [0.0] * n, [0.0] * n
            kepala_r = list(c[3][1])
            baris = kepala_r + [r for body in c[4] for r in list(body[2]) + list(body[3])]
            sisa_rs = [0] * n           # FIX-TABEL-ROWSPAN: kolom yang masih ditempati sel gabungan vertikal
            for i_r, row in enumerate(baris):
                kol = 0
                if i_r == len(kepala_r):
                    sisa_rs = [0] * n
                for cell in row[1]:
                    while kol < n and sisa_rs[kol] > 0:
                        kol += 1
                    span = cell[3]
                    for c_ in range(kol, min(n, kol + span)):
                        sisa_rs[c_] = max(sisa_rs[c_], cell[2])
                    if span == 1 and kol < n:
                        for blk in cell[4]:
                            if blk.get("t") == "Table":
                                sub = lebar_isi_tabel([blk], ukuran, inset)
                                if sub:
                                    nat[kol] = max(nat[kol], sub[0])
                                    mn[kol] = max(mn[kol], sub[1])
                                continue
                            t = stringify(inlines_of(blk) or blk).strip()
                            # judul kolom boleh pecah (seperti di Word): hanya kata terpanjangnya
                            # yang menentukan lebar alami
                            if i_r >= len(kepala_r):
                                nat[kol] = max(nat[kol], ukur(t))
                            else:
                                max_w = max((ukur(w) * 1.06 for w in re.split(r"[ \t\n\xad]+", t)), default=0)
                                nat[kol] = max(nat[kol], max_w, ukur(t) / 2.0)
                            for w in re.split(r"[ \t\n\xad]+", t):  # spasi tak-putus = satu kata; soft hyphen = titik patah
                                mn[kol] = max(mn[kol], ukur(w))
                    kol += span
                sisa_rs = [max(0, x - 1) for x in sisa_rs]
            alami_t = max(alami_t, sum(x + inset for x in nat))
            minimum_t = max(minimum_t, sum(x + inset for x in mn))
            kolom_t.append(([x + inset for x in nat], [x + inset for x in mn]))
        return alami_t, minimum_t, kolom_t
    except Exception:
        return None


def render_chart_docx(docx_in, workdir, laporan, font_chart=None, ukuran_chart=11.0):
    """FIX-CHART: chart asli Word (c:chart) tidak dibaca pandoc. Tiap chart dirender
    LibreOffice dari docx mini (hanya paragraf chart itu), dipangkas ke kotak grafiknya,
    lalu digantikan gambar PNG di salinan docx. Ukuran tampil (wp:extent) tidak diubah."""
    import zipfile
    with zipfile.ZipFile(docx_in) as z:
        isi = {n: z.read(n) for n in z.namelist()}
    doc = isi["word/document.xml"].decode("utf-8")
    # FIX-SMARTART: SmartArt (diagram) dan kanvas gambar Word juga tidak dibaca pandoc -> dirender
    # dengan cara yang sama; ukuran tampilnya tetap ukuran Word (tanpa penyesuaian font chart).
    pola_chart = (r'<a:graphic [^>]*>\s*<a:graphicData uri="http://schemas.(?:openxmlformats.org/drawingml/2006/'
                  r'(?:chart|diagram)|microsoft.com/office/word/2010/wordprocessingCanvas)">.*?</a:graphic>')
    if not re.search(pola_chart, doc, flags=re.S):
        return docx_in, 0
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    awal, akhir = doc.index("<w:body>") + len("<w:body>"), doc.index("</w:body>")
    body = doc[awal:akhir]
    sect = re.search(r"<w:sectPr[ >].*?</w:sectPr>", body, flags=re.S)
    sect = sect.group(0) if sect else ""
    # FIX-CHART-HEADER: header/footer naskah penulis ikut tercetak dan terpotong ke gambar chart
    sect = re.sub(r"<w:(?:header|footer)Reference\b[^>]*/>|<w:titlePg\s*/>", "", sect)
    paras = re.findall(r"<w:p[ >].*?</w:p>", body, flags=re.S)
    para_chart = [x for x in paras if re.search(pola_chart, x, flags=re.S)]
    doc_asli = doc          # FIX-CHART-MINI: doc mutasi mengganti chart1; mini docx harus dari aslinya
    rels = isi["word/_rels/document.xml.rels"].decode("utf-8")
    # FIX-CHART-TNR: ganti jenis huruf chart ke font narasi, tetapi JANGAN paksa ukuran (sz).
    # Ukuran asli dipertahankan agar LibreOffice menata label pada kanvas chart aslinya;
    # memaksa 11 pt membuat label sumbu/legenda bertabrakan dan legenda terpotong.
    if font_chart:
        tx = ('<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr>'
              '<a:latin typeface="{f}"/><a:ea typeface="{f}"/><a:cs typeface="{f}"/>'
              '</a:defRPr></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>').format(f=font_chart)
        for nama in [n for n in isi if re.match(r"word/charts/chart\d+\.xml$", n)]:
            x = isi[nama].decode("utf-8")
            x = re.sub(r'(<a:(?:latin|ea|cs) typeface=")[^"]*(")', r"\g<1>{}\2".format(font_chart), x)
            ujung = x.rfind("</c:chart>")   # FIX-CHART-TNR2: jangan timpa variabel akhir (</w:body>)
            ekor = re.sub(r"<c:txPr>.*?</c:txPr>", "", x[ujung:], flags=re.S)
            if "<c:externalData" in ekor:
                ekor = ekor.replace("<c:externalData", tx + "<c:externalData", 1)
            elif "<c:printSettings" in ekor:
                ekor = ekor.replace("<c:printSettings", tx + "<c:printSettings", 1)
            else:
                ekor = ekor.replace("</c:chartSpace>", tx + "</c:chartSpace>", 1)
            isi[nama] = (x[:ujung] + ekor).encode("utf-8")
    ctypes = isi["[Content_Types].xml"].decode("utf-8")
    if 'Extension="png"' not in ctypes:
        ctypes = ctypes.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
    tmp = os.path.join(workdir, "_chart")
    os.makedirs(tmp, exist_ok=True)
    n = 0
    for k, par in enumerate(para_chart, 1):
        jenis_g = "chart" if "drawingml/2006/chart" in par else "diagram"
        mini = os.path.join(tmp, "{}{}.docx".format(jenis_g, k))
        with zipfile.ZipFile(mini, "w", zipfile.ZIP_DEFLATED) as z:
            for nama, data in isi.items():
                if nama == "word/document.xml":
                    # FIX-CHART-MINI: indeks awal/akhir dihitung pada doc ASLI; memakai doc yang
                    # sudah dimutasi membuat mini docx chart berikutnya rusak (gagal dimuat).
                    data = (doc_asli[:awal] + par + sect + doc_asli[akhir:]).encode("utf-8")
                z.writestr(nama, data)
        rc, out, err = jalankan_soffice(mini, tmp)
        pdf = mini[:-5] + ".pdf"
        if rc != 0 or not os.path.isfile(pdf):
            laporan["masalah"].append("Chart ke-{} gagal dirender LibreOffice: {}".format(k, (err or out)[-200:]))
            continue
        with fitz.open(pdf) as pdf_doc:      # FIX-MEMORI-FITZ
            hal = pdf_doc[0]
            kotak = None
            for g in hal.get_drawings():
                kotak = g["rect"] if kotak is None else kotak | g["rect"]
            for blk in hal.get_text("dict")["blocks"]:
                kotak = fitz.Rect(blk["bbox"]) if kotak is None else kotak | fitz.Rect(blk["bbox"])
            if kotak is None or kotak.is_empty:
                laporan["masalah"].append("Chart ke-{} kosong setelah dirender".format(k))
                continue
            nama_png = "{}_render_{}.png".format(jenis_g, k)
            hal.get_pixmap(dpi=300, clip=kotak + (-2, -2, 2, 2)).save(os.path.join(tmp, nama_png))
            # FIX-UKURAN-CHART: skala tampil = font narasi / font terbanyak asli chart.
            # Ukuran asli dipakai sebagai dasar agar layout internal chart (label/legenda)
            # tetap proporsional; template membatasi lebar tampil ke LEBAR-TEKS.
            ukuran = []
            for blk in hal.get_text("dict")["blocks"]:
                for baris in blk.get("lines", []):
                    for sp in baris["spans"]:
                        ukuran += [sp["size"]] * len(sp["text"].strip())
            median = sorted(ukuran)[len(ukuran) // 2] if ukuran else None
            skala = UKURAN_NARASI_PT / median if median else 1.0
            if jenis_g == "chart":
                alami = kotak.width + 4
                CHART_LEBAR[nama_png] = alami * skala
                if median:
                    # FIX-QA-CHART: ukuran efektif setelah template membatasi lebar ke LEBAR-TEKS
                    tampil = min(alami * skala, LEBAR_TEKS_PT)
                    CHART_EFEKTIF[nama_png] = median * tampil / alami
        with open(os.path.join(tmp, nama_png), "rb") as f:
            isi["word/media/" + nama_png] = f.read()
        rid = "rIdChartRender{}".format(k)
        rels = rels.replace("</Relationships>",
            '<Relationship Id="{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/{}"/></Relationships>'.format(rid, nama_png))
        gambar = ('<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                  '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                  '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                  '<pic:nvPicPr><pic:cNvPr id="0" name="{n}"/><pic:cNvPicPr/></pic:nvPicPr>'
                  '<pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{r}"/>'
                  '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                  '<pic:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
                  '</a:graphicData></a:graphic>').format(n=nama_png, r=rid)
        par_baru = re.sub(pola_chart, lambda m: gambar, par, count=1, flags=re.S)
        doc = doc.replace(par, par_baru, 1)
        n += 1
    isi["word/document.xml"] = doc.encode("utf-8")
    isi["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    isi["[Content_Types].xml"] = ctypes.encode("utf-8")
    keluar = os.path.join(workdir, "_naskah_chart.docx")
    with zipfile.ZipFile(keluar, "w", zipfile.ZIP_DEFLATED) as z:
        for nama, data in isi.items():
            z.writestr(nama, data)
    return keluar, n


def ubah_emf(workdir, laporan):
    """FIX-EMF: gambar EMF/WMF (vektor Windows) tidak dapat ditampilkan Typst. Tiap berkas
    dikonversi lewat LibreOffice ke PDF, dirender 300 dpi, lalu dipotong ke isi gambarnya
    (LibreOffice menaruhnya di halaman A4) dan disimpan sebagai PNG di folder yang sama."""
    berkas = [f for f in glob.glob(os.path.join(workdir, "media", "**", "*"), recursive=True)
              if f.lower().endswith((".emf", ".wmf"))]
    if not berkas:
        return
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    from PIL import Image, ImageChops
    tmp = os.path.join(workdir, "_emf")
    os.makedirs(tmp, exist_ok=True)
    for f in berkas:
        rc, _, err = jalankan_soffice(f, tmp)
        pdf = os.path.join(tmp, os.path.splitext(os.path.basename(f))[0] + ".pdf")
        if rc != 0 or not os.path.isfile(pdf):
            laporan["masalah"].append("Gambar {} (EMF/WMF) gagal dikonversi: {}".format(
                os.path.basename(f), err.strip()[-200:]))
            continue
        with fitz.open(pdf) as pdf_doc:      # FIX-MEMORI-FITZ
            pix = pdf_doc[0].get_pixmap(dpi=300)
        im = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        bb = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
        if bb:
            im = im.crop((max(0, bb[0] - 4), max(0, bb[1] - 4),
                          min(im.width, bb[2] + 4), min(im.height, bb[3] + 4)))
        im.save(os.path.splitext(f)[0] + ".png", dpi=(300, 300))
    laporan["peringatan"].append("{} gambar EMF/WMF dikonversi ke PNG 300 dpi".format(len(berkas)))


def gabung_kolom_apung(teks, keputusan):
    """FIX-APUNG-GABUNG: objek selebar halaman yang mengapung tidak perlu memutus kolom.
    Pola ']\n<objek>...\n#kolom[\n' digabung sehingga float dipicu dari dalam kolom dan
    teks mengalir tanpa celah; objek tetap naik ke atas halaman berikut."""
    for k, v in keputusan.items():
        if v != "apung":
            continue
        i = teks.find('"@K{}@"'.format(k))
        p = teks.rfind("\n", 0, i) + 1                      # awal baris objek
        q = teks.find("\n#kolom[\n", i)                    # pembuka segmen sesudahnya
        if p < 2 or teks[p - 2:p] != "]\n" or q < 0:
            continue                                          # objek satu kolom: tak diputus
        lain = teks.find('"@K', i + 3)
        if lain != -1 and lain < q:
            continue
        teks = teks[:p - 2] + teks[p:q + 1] + teks[q + len("\n#kolom[\n"):]
    return teks


def kueri_posisi(main):
    """Semua metadata berlabel -> {label tanpa <> (FIX-KUNCI-LABEL): (nilai, halaman, x, y)}."""
    kueri = ('query(metadata).filter(it => it.has("label")).map(it => (str(it.label), it.value, '
             'it.location().page(), it.location().position().x.pt(), it.location().position().y.pt()))')
    rc, out, err = jalankan([TYPST, "eval", "--root", BASE, "--font-path",
                             os.path.join(BASE, "fonts"), "--in", main, kueri])
    if rc != 0:
        return None, err[-300:]
    return {lbl: (nilai, hal, x, y) for lbl, nilai, hal, x, y in json.loads(out)}, ""


def blok_objek(teks, k):
    """(awal, akhir) teks pemanggilan objek k (#gambar satu baris; #tabel s.d. baris ']')."""
    i = teks.find('"@K{}@"'.format(k))
    p = teks.rfind("\n", 0, i) + 1
    if teks.startswith("#tabel(", p):
        q = teks.find("\n]\n", i)
        return p, q + 3
    q = teks.find("\n", i)
    return p, q + 1


def objek_penuh(panggil):
    """FIX-OBJEK-PENUH: True bila objek selebar halaman (aturan sama dengan template)."""
    if "penuh: true" in panggil:
        return True
    if "penuh: false" in panggil:
        return False
    m = re.search(r"lebar-word: ([\d.]+)pt", panggil)
    if m:
        return float(m.group(1)) > LEBAR_KOLOM_PT * 1.25
    m = re.search(r"jumlah-kolom: (\d+)", panggil)
    return bool(m) and int(m.group(1)) > 3


def objek_penuh_teks(teks, k):
    """FIX-UKUR-PENUH: True bila objek k pada teks body (nomor: k) selebar halaman.
    Dipakai ukur_kolom untuk mengecualikan isi tabel/gambar selebar halaman dari
    pengukuran kolom kiri/kanan (sel tabel tidak boleh menutupi kolom kiri yang pendek)."""
    m = re.search(r"#(?:gambar|tabel)\([^\n]*\bnomor: {}\b".format(k), teks)
    if not m:
        return False
    i = teks.rfind("\n", 0, m.start()) + 1
    j = teks.find("\n", m.start())
    return objek_penuh(teks[i:j if j >= 0 else len(teks)])


def objek_frozen_teks(teks, k):
    """True bila objek k pada teks body (nomor: k) memiliki parameter frozen: true, freeze-x:, atau freeze-y:."""
    if not teks:
        return False
    pola = re.compile(r"#(?:gambar|tabel|tabel-panjang)\((.*?)\)", re.DOTALL)
    for m in pola.finditer(teks):
        isi_arg = m.group(1)
        if re.search(r"\bnomor:\s*{}\b".format(k), isi_arg):
            if re.search(r"\bfrozen:\s*true\b", isi_arg):
                return True
            for fz_m in re.finditer(r"\bfreeze-([xy]):\s*(-?[\d.]+)pt", isi_arg):
                try:
                    if abs(float(fz_m.group(2))) > 0.05:
                        return True
                except ValueError:
                    pass
    return False


def pm_beku_teks(teks):
    """Set nomor pm (int) yang dibungkus #beku(...) dalam body.typ."""
    if not teks or "#beku(" not in teks:
        return set()
    hasil = set()
    idx = 0
    while True:
        pos_beku = teks.find("#beku(", idx)
        if pos_beku < 0:
            break
        kp = pos_beku + len("#beku")
        kedalaman = 1
        j = kp + 1
        while j < len(teks) and kedalaman > 0:
            if teks[j] == "(":
                kedalaman += 1
            elif teks[j] == ")":
                kedalaman -= 1
            j += 1
        if kedalaman != 0:
            idx = pos_beku + 1
            continue
        kb = teks.find("[", j)
        if kb < 0:
            idx = j
            continue
        kedalaman = 1
        k = kb + 1
        while k < len(teks) and kedalaman > 0:
            if teks[k] == "[":
                kedalaman += 1
            elif teks[k] == "]":
                kedalaman -= 1
            k += 1
        if kedalaman == 0:
            blok_isi = teks[kb + 1:k - 1]
            for m in re.finditer(r"#pm\((\d+)\)", blok_isi):
                hasil.add(int(m.group(1)))
            idx = k
        else:
            idx = kb + 1
    return hasil


def tabel_tinggi(pos, k, batas=330.0):
    """FIX-TABEL-TINGGI: tabel (di tempat) yang tidak muat dalam satu halaman (tinggi > separuh
    area teks 660 pt) atau yang sudah terpotong lintas halaman tidak boleh dijadikan float
    (float tidak bisa dipotong sehingga isinya meluber keluar halaman); tetap di aliran teks
    dan dipotong lintas halaman. Tinggi dihitung dari penanda awal/akhir tabel."""
    ATAS, BAWAH = 102.4, 841.89 - 79.0
    P, A = pos.get("objek-posisi-{}".format(k)), pos.get("objek-akhir-{}".format(k))
    I = pos.get("objek-info-{}".format(k))
    if not P or not A:
        return False
    awal = I if I and I[1] >= P[1] else P
    if A[1] > awal[1]:
        return True                 # FIX-TABEL-TINGGI: sudah terpotong -> jangan dijadikan float
    tinggi = A[3] - awal[3]
    return tinggi > batas


def judul_bagian_sesudah(body, n):
    """FIX-OBJEK-JUDUL: nomor penanda (#pm) judul bagian utama (= JUDUL) pertama pada/sesudah
    paragraf n; objek tidak boleh tampil melewati judul ini."""
    if not n:
        return None
    nomor = [int(m.group(1)) for m in re.finditer(r"^= #pm\((\d+)\);", body, flags=re.M)]
    return min([x for x in nomor if x >= n], default=None)


# FIX-REF-SEPARAGRAF: beberapa entri referensi diketik dalam SATU paragraf tanpa Enter
RE_REF_TAHUN = re.compile(r"\((?:\d{4}[a-z]?|n\.\s?d\.)(?:,[^)]{0,30})?\)")
RE_REF_PENULIS = re.compile(r"[A-ZÀ-Ý][a-zà-ÿ'’\-]+(?:[\s\-][A-ZÀ-Ý][a-zà-ÿ'’\-]+)*,\s(?:[A-Z]\.\s?)+")
_KATA_ORG = r"(?:[A-ZÀ-Ý][\w&'’\-]*,?|of|and|for|on|the|in|&|\([A-Z]{2,}\))"
RE_REF_ORG = re.compile(_KATA_ORG + r"(?:\s" + _KATA_ORG + r"){0,14}\.\s\((?:\d{4}|n\.\s?d\.)")


def pecah_entri_referensi(inl):
    """FIX-REF-SEPARAGRAF: pisahkan entri referensi yang tergabung dalam satu paragraf. Titik pisah =
    awal token sesudah akhir entri (titik+spasi atau URL/DOI) yang berbentuk "Nama, I." atau nama lembaga
    "... . (" dan diikuti tahun dalam kurung; bagian sebelumnya harus sudah memuat tahun."""
    teks, awal = "", []
    for x in inl:
        awal.append(len(teks))
        teks += stringify([x]) if x.get("t") not in ("Space", "SoftBreak", "LineBreak") else " "
    titik, mulai = [], 0
    for i in range(2, len(inl)):
        x, sbl = inl[i], inl[i - 1]
        if x.get("t") not in ("Str", "Emph", "Strong", "Span") or sbl.get("t") not in ("Space", "SoftBreak", "LineBreak"):
            continue
        prev = inl[i - 2]
        pt = teks[awal[i - 2]:awal[i - 1]].strip()
        akhir_entri = pt.endswith((".", ")")) or prev.get("t") == "Link" or \
            re.search(r"https?://|^www\.|doi\.org|^doi:", pt)
        if not akhir_entri:
            continue
        sisa = teks[awal[i]:]
        if not (RE_REF_PENULIS.match(sisa) or RE_REF_ORG.match(sisa)):
            continue
        th = RE_REF_TAHUN.search(sisa[:220])
        if not th or not RE_REF_TAHUN.search(teks[awal[mulai]:awal[i]]):
            continue
        titik.append(i)
        mulai = i
    if not titik:
        return [inl]
    hasil, a = [], 0
    for t in titik:
        bag = inl[a:t]
        while bag and bag[-1].get("t") in ("Space", "SoftBreak", "LineBreak"):
            bag = bag[:-1]
        hasil.append(bag)
        a = t
    hasil.append(inl[a:])
    return hasil


def kosakata_naskah(items):
    """FIX-SUBSUB-KALIMAT: hitungan (kata ditulis huruf kecil, kata ditulis kapital di TENGAH kalimat)
    dari paragraf isi naskah -> membedakan nama diri (Sulu, Indonesia) dari kata biasa."""
    from collections import Counter
    kecil, tengah = Counter(), Counter()
    for it in items:
        if it.get("jenis") != "para" or not it.get("inl"):
            continue
        kata = re.findall(r"[^\s]+", stringify(it["inl"]))
        for i, w in enumerate(kata):
            for bag in re.split(r"[-–—/]", re.sub(r"^[\W_]+|[\W_]+$", "", w)):
                if not bag:
                    continue
                if bag.islower():
                    kecil[bag] += 1
                elif i > 0 and bag[:1].isupper() and not re.search(r"[.!?:][\"'”’)]*$", kata[i - 1]):
                    tengah[bag] += 1
    return kecil, tengah


def kalimat_subsub(inl, kosakata):
    """FIX-SUBSUB-KALIMAT: sub-sub-judul dalam Sentence case (hanya huruf pertama kata pertama kapital),
    tanpa mengubah singkatan (CBM, ASEAN, ReCAAP) dan nama diri (kata yang di naskah lebih sering ditulis
    kapital di tengah kalimat, atau yang menyambung nama diri sebelumnya: "Sulu Sea")."""
    kecil, tengah = kosakata
    st = {"pertama": True, "nama": False}

    def satu(ib):
        """True bila bagian kata ini diubah ke huruf kecil."""
        if not ib or not ib[:1].isupper() or len(ib) < 2 or ib[1:] != ib[1:].lower():
            st["nama"] = bool(ib) and ib[:1].isupper()          # singkatan / CamelCase = nama
            return False
        diri = tengah[ib] > 0 and (tengah[ib] >= kecil[ib.lower()] or st["nama"])
        st["nama"] = diri
        return not diri

    def ubah(w):
        if not re.sub(r"^[\W_]+|[\W_]+$", "", w):
            return w
        bagian = re.split(r"([-–—/])", w)
        hasil = []
        for i, b in enumerate(bagian):
            ib = re.sub(r"^[\W_]+|[\W_]+$", "", b)
            if st["pertama"] and ib:
                st["pertama"] = False                        # huruf pertama kata pertama tetap
                st["nama"] = False
            elif ib and satu(ib):
                b = b.replace(ib, ib.lower(), 1)
            hasil.append(b)
        return "".join(hasil)

    def jalan(xs):
        out = []
        for x in xs:
            t = x.get("t")
            if t == "Str":
                out.append({"t": "Str", "c": ubah(x["c"])})
            elif t in ("Emph", "Strong", "Underline", "SmallCaps"):
                out.append({"t": t, "c": jalan(x["c"])})
            elif t == "Span":
                out.append({"t": t, "c": [x["c"][0], jalan(x["c"][1])]})
            else:
                out.append(x)
        return out
    return jalan(inl)


def judul_bagian_sebelum(body, n):
    """FIX-OBJEK-JUDUL: penanda judul bagian utama terakhir sebelum paragraf n (awal bagian objek)."""
    if not n:
        return None
    nomor = [int(m.group(1)) for m in re.finditer(r"^= #pm\((\d+)\);", body, flags=re.M)]
    return max([x for x in nomor if x < n], default=None)


def kolom_akhir_tidak_seimbang(teks):
    """Akhir dokumen mengisi kiri dahulu; sisa ruang hanya boleh di kanan."""
    pembuka = list(re.finditer(r"^#kolom(?:-lanjut)?(?:\([^\n]*\))?\[", teks, re.M))
    if not pembuka:
        return teks
    m = pembuka[-1]
    return teks[:m.start()] + "#kolom-lanjut[" + teks[m.end():]


def susun_teks(body_dasar, keputusan, hapus=None, akhir_seimbang=False):
    """FIX-ATURAN-OBJEK: terapkan keputusan ke teks body.
    tempat = di posisi naskah; lanjut = segmen teks SEBELUM objek tidak diseimbangkan
    (kolom kiri penuh, kanan boleh menggantung) sehingga objek mulai di atas halaman berikut;
    kecil = gambar raster diperkecil (skala); tunda = float dipicu dari paragraf pertama
    halaman berikut (objek tidak pernah mendahului teks sebelumnya). hapus = objek yang
    dihilangkan sementara (probe untuk mencari paragraf target penundaan)."""
    teks = body_dasar
    # FIX-CELAH-GAMBAR-DAFTAR: float selebar halaman yang langsung diikuti daftar: celah 11 pt
    # yang semula di atas daftar dipindah ke sesudah float, bukan dihapus, agar jarak
    # paragraf->daftar tetap ada tanpa membuat grup daftar tidak muat di dasar kolom.
    teks = re.sub(r'#celah\(\)\n(#gambar\([^\n]*keputusan: (?:"@K\d+@"|"apung")[^\n]*\))\n\n(#butir\()',
                  r'\1\n\n#celah()\n\2', teks)
    # FIX-TABEL-GESER & FIX-SANDWICH-GESER: objek selebar halaman tetap di aliran teks tetapi
    # dimulai di paragraf target; mendukung beberapa objek berurutan menuju target yang sama
    # (mis. Figure 1 + Table 4 menempel rapat setelah teks di puncak halaman).
    daftar_geser = []
    for k in sorted(keputusan):
        kp = keputusan[k]
        if kp["jenis"] not in ("geser", "pindah_pita") or k == hapus or not kp.get("target"):
            continue
        p, q = blok_objek(teks, k)
        if teks[p - 2:p] != "]\n" or not teks.startswith("#kolom[\n", q):
            continue
        objek = teks[p:q]
        teks = teks[:p - 2] + teks[q + len("#kolom[\n"):]
        daftar_geser.append((k, kp["target"], objek))
    for k, tgt_k, objek in daftar_geser:
        t = teks.find("#pm({});".format(tgt_k))
        if t < 0:
            continue
        t = teks.rfind("\n", 0, t) + 1
        if t >= len("#celah()\n") and teks[t - len("#celah()\n"):t] == "#celah()\n":
            t -= len("#celah()\n")
        teks = teks[:t] + "]\n" + objek + "#kolom[\n" + teks[t:]
    teks = re.sub(r"#kolom\[\s*\]\n", "", teks)
    # FIX-PITA-TABEL-ATAS: pita teks di atas tabel lintas halaman dipindah ke segmen sesudah tabel
    for k in sorted(keputusan):
        kp = keputusan[k]
        if kp["jenis"] != "pindah_pita" or k == hapus or not kp.get("n_pm"):
            continue
        teks = pindah_pita_atas_tabel(teks, k, kp["mode"], kp["n_pm"], kp.get("teks_baris", ""))
    # FIX-URUTAN-TUNDA: urut naik -> objek tunda dengan paragraf pemicu yang sama tersusun sesuai
    # urutan naskah (yang disisipkan belakangan berada sesudah yang lebih dulu)
    for k in sorted(keputusan):
        kp = keputusan[k]
        if kp["jenis"] != "tunda" and k != hapus:
            continue
        teks = gabung_kolom_apung(teks, {k: "apung"})
        p, q = blok_objek(teks, k)
        objek, teks = teks[p:q], teks[:p] + teks[q:]
        if k == hapus:
            continue
        t = teks.find("#pm({});".format(kp["target"]))
        if t < 0:
            teks = teks[:p] + objek + teks[p:]          # target tak ada: kembalikan
            continue
        t = teks.rfind("\n", 0, t) + 1
        teks = teks[:t] + objek + "\n" + teks[t:]
    for k in sorted(keputusan):
        kp = keputusan[k]
        if kp["jenis"] == "lanjut" and k != hapus:
            p, _ = blok_objek(teks, k)
            s = teks.rfind("#kolom[", 0, p)
            if s >= 0:
                teks = teks[:s] + "#kolom-lanjut[" + teks[s + len("#kolom["):]
        if kp["jenis"] in ("atas", "bawah") and k != hapus:          # FIX-PITA-SUSUN
            teks = gabung_kolom_apung(teks, {k: "apung"})
        if kp["jenis"] == "tunda":
            # FIX-TUNDA-BAWAH: gambar satu kolom yang ditunda diletakkan di dasar kolom,
            # bukan float di puncak, agar teks mengisi kolom di atasnya.
            blok_k = body_dasar[slice(*blok_objek(body_dasar, k))]
            nilai = "bawah" if blok_k.startswith("#gambar(") and not objek_penuh(blok_k) else "apung"
        else:
            nilai = kp["jenis"] if kp["jenis"] in ("atas", "bawah") else "tempat"
        # FIX-OPTIMASI-SKALA: skala hanya untuk #gambar (#tabel tidak punya argumen skala)
        p_k = teks.find('"@K{}@"'.format(k))
        gbr = teks.startswith("#gambar(", teks.rfind("\n", 0, p_k) + 1)
        tambahan = ', skala: {:.3f}'.format(kp["skala"]) if "skala" in kp and gbr else ""
        teks = teks.replace('"@K{}@"'.format(k), '"{}"{}, nomor: {}'.format(nilai, tambahan, k))  # FIX-NOMOR-OBJEK
    # FIX-CELAH-AWAL-KOLOM: celah sebelum judul tepat di awal segmen kolom (sesudah objek lebar)
    # membuat judul turun satu baris dan tidak sejajar dengan baris pertama kolom sebelahnya
    teks = re.sub(r"(^#kolom(?:-lanjut)?(?:\([^)\n]*\))?\[\n)#celah\(\)\n", r"\1", teks, flags=re.M)
    # FIX-CELAH-GAMBAR-DAFTAR-AKHIR: float yang ditunda disisipkan sebelum #pm daftar, sehingga
    # celah yang semula di atas daftar berpindah ke atas float. Pindahkan ke sesudah float
    # agar jarak paragraf->daftar tetap terlihat.
    teks = re.sub(r'#celah\(\)\n(#gambar\([^\n]*keputusan: "[^"]+"[^\n]*\))\n\n(#butir\()',
                  r'\1\n\n#celah()\n\2', teks)
    return teks if akhir_seimbang else kolom_akhir_tidak_seimbang(teks)


def tabel_baris_body(teks, k):
    """FIX-TABEL-PANJANG-INFLOW: jumlah baris tabel sebenarnya pada pemanggilan objek k.
    Pandoc dapat menulis satu baris tabel per baris sumber (sel inline: "[..], [..]") ATAU
    satu sel per baris (table.cell(...)). Hitung sesuai format yang dipakai agar tidak
    menghitung sel sebagai baris atau membagi baris yang sudah benar."""
    blk = teks[slice(*blok_objek(teks, k))]
    if not blk.startswith("#tabel("):
        return 0
    i = blk.find("#table(")
    if i < 0:
        return 0
    m_kol = re.search(r"jumlah-kolom:\s*(\d+)", blk[:i])
    jml_kol = max(1, int(m_kol.group(1))) if m_kol else 1
    isi = blk[i:]
    # Format baris-per-baris: tiap baris tabel dimulai dengan "[" (sel inline).
    baris_inline = sum(1 for ln in isi.splitlines() if ln.strip().startswith("["))
    if baris_inline:
        return baris_inline
    # Format sel-per-baris: hitung table.cell(...) lalu bagi dengan jumlah kolom.
    jml_sel = sum(1 for ln in isi.splitlines()
                  if ln.strip().startswith(("table.cell(", "[")))
    return int(math.ceil(jml_sel / float(jml_kol)))


def label_dan_nomor(body_dasar, k):
    """FIX-URUTAN-LABEL: (keluarga, nomor) objek dari label "Figure 3." / "Table 2."."""
    blk = body_dasar[slice(*blok_objek(body_dasar, k))]
    m = re.search(r"label: \[(Figure|Gambar|Table|Tabel)\s+(\d+)", blk)
    if not m:
        return None
    fam = "fig" if m.group(1).lower() in ("figure", "gambar") else "tab"
    return fam, int(m.group(2))


def urutan_label_buruk(body_dasar, pos):
    """FIX-URUTAN-LABEL: pasangan (k_bawah, k_atas) yang objek bernomor lebih tinggi tampil
    di halaman/posisi lebih awal daripada objek bernomor lebih rendah pada keluarga yang sama.
    Hanya berlaku untuk Figure/Gambar (aturan urutan gambar), bukan tabel."""
    fams = {}
    for k in sorted(pos):
        m = re.match(r"objek-info-(\d+)$", k)
        if not m:
            continue
        kk = int(m.group(1))
        ln = label_dan_nomor(body_dasar, kk)
        if ln and ln[0] == "fig":
            fams.setdefault("fig", []).append((ln[1], kk))
    for lst in fams.values():
        lst.sort()
        for (n1, k1), (n2, k2) in zip(lst, lst[1:]):
            I1, I2 = pos.get("objek-info-{}".format(k1)), pos.get("objek-info-{}".format(k2))
            if I1 and I2 and (I2[1], I2[3]) < (I1[1], I1[3]):
                yield k1, k2


def putuskan_objek(body_dasar, body_path, workdir, pdf, keputusan, peta_objek):
    """FIX-ATURAN-OBJEK: kompilasi berulang; objek dinilai berurutan dan hanya objek PERTAMA
    yang belum benar yang dinaikkan satu tingkat: tempat -> lanjut -> kecil (raster) -> tunda.
    Tabel: tempat -> lanjut (tabel boleh terpotong, tidak pernah ditunda)."""
    BAWAH, ATAS = 841.89 - 79.0, 102.4
    cetak("4a. Menentukan posisi objek (di tempat / atas / bawah / halaman berikut)")
    log = LogCetak()
    main = os.path.join(workdir, "main.typ")
    sesudah = {k: n for (k, _, n) in peta_objek}
    coba_atas = set()       # FIX-ATAS-SAMA: objek yang sudah dicoba ke atas halaman penandanya
    coba_lubang = set()
    # FIX-PITA-ATURAN: objek selebar halaman & PDF ukur untuk pemeriksaan pita teks
    penuh_obj = {k: objek_penuh(body_dasar[slice(*blok_objek(body_dasar, k))]) for k in keputusan}
    # FIX-TABEL-PANJANG-INFLOW: tabel >=15 baris selalu in-flow; biarkan Typst memotongnya
    tabel_panjang = {k for k in keputusan if tabel_baris_body(body_dasar, k) >= 15}
    pdf_pita = os.path.join(workdir, "_pita.pdf")

    def tulis(teks):
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(teks)

    for putaran in range(6 * len(keputusan) + 4):
        teks = susun_teks(body_dasar, keputusan)
        tulis(teks)
        pos, err = kueri_posisi(main)
        if pos is None:
            log.append("Kueri posisi objek gagal: " + err)
            # FIX-TIDAK-KONVERGEN: objek di tempat yang posisinya berosilasi (tata letak Typst
            # tidak konvergen) -> coba objek itu ke atas, lalu dasar, halaman yang sama; objek
            # dicoba berurutan (selebar halaman dulu) sampai dokumen dapat disusun lagi.
            pulih = None
            calon_k = sorted(
                (k for k in keputusan
                 if keputusan[k]["jenis"] in ("tempat", "kecil")
                 and not body_dasar[slice(*blok_objek(body_dasar, k))].startswith("#tabel(")),
                key=lambda k: (not penuh_obj.get(k), k))
            for k in calon_k:
                for arah in ("atas", "bawah"):
                    coba = dict(keputusan)
                    coba[k] = dict(keputusan[k], jenis=arah)
                    coba[k].pop("target", None)
                    tulis(susun_teks(body_dasar, coba))
                    pos, err2 = kueri_posisi(main)
                    if pos is not None:
                        pulih = (k, coba[k])
                        break
                if pulih:
                    break
            if not pulih:
                break
            keputusan[pulih[0]] = pulih[1]
            log.append("Objek {} -> {} (tata letak di tempat tidak konvergen)".format(pulih[0], pulih[1]["jenis"]))
            continue
        ubah = None
        luber_k = {k_l for k_l, _, _ in objek_luber(pos)}       # FIX-OBJEK-LUBER
        for k in sorted(keputusan):
            kp = keputusan[k]
            if k in tabel_panjang:
                # FIX-TABEL-PANJANG-INFLOW: tabel panjang tidak boleh mengapung/geser;
                # paksa in-flow agar Typst memotongnya di batas halaman. "pindah_pita" juga
                # in-flow (tabel tetap di tempat, hanya teks pita yang dipindah).
                if kp.get("jenis") not in ("tempat", "geser", "pindah_pita", "bawah"):
                    ubah = (k, {"jenis": "tempat"})
                    break
                continue
            # FIX-ATAS-SAMA: objek yang dipicu dari paragraf pertama halaman penandanya harus benar-benar
            # tampil di halaman itu; bila tidak (tidak muat), barulah ditunda ke atas halaman berikutnya
            if kp["jenis"] == "tunda" and kp.get("maju_hal") and kp.get("lanjut"):
                I_m = pos.get("objek-info-{}".format(k))
                if not (I_m and I_m[1] == kp["maju_hal"] and k not in luber_k):
                    h_jdl_m = judul_bagian_sesudah(body_dasar, sesudah.get(k))
                    if h_jdl_m is not None and kp["lanjut"] > h_jdl_m:
                        ubah = (k, {"jenis": "tunda", "target": kp["target"], "maju": True})
                        log.append("Objek {}: ditahan di atas hlm {} agar tidak melewati judul bagian".format(
                            k, kp["maju_hal"]))
                    else:
                        ubah = (k, {"jenis": "tunda", "target": kp["lanjut"]})
                        log.append("Objek {}: tidak muat di atas hlm {} -> ke atas halaman berikutnya".format(
                            k, kp["maju_hal"]))
                    break
                continue
            if kp["jenis"] in ("tunda", "geser", "pindah_pita"):
                continue
            P, I = pos.get("objek-posisi-{}".format(k)), pos.get("objek-info-{}".format(k))
            if not P or not I:
                continue
            (_, hp, xp, yp), (nilai, ho, xo, yo) = P, I
            kolom_obj = not objek_penuh(body_dasar[slice(*blok_objek(body_dasar, k))])   # FIX-OBJEK-PENUH
            if kolom_obj:     # objek satu kolom: bandingkan per kolom
                kol = lambda x: 0 if x < 300 else 1
                ok = (ho, kol(xo)) == (hp, kol(xp)) and yo >= yp - 1
                kol_brk = (hp, 1) if kol(xp) == 0 else (hp + 1, 0)
                ok = ok or (BAWAH - yp < 11.5 and (ho, kol(xo)) == kol_brk and yo < ATAS + 30)
            else:
                ok = (ho == hp and yo >= yp - 1) or (BAWAH - yp < 11.5 and ho == hp + 1 and yo < ATAS + 30)
            if kp["jenis"] in ("atas", "bawah"):      # FIX-PITA-ATURAN: cukup di halaman penandanya
                ok = ho == hp
            if ok and k not in luber_k:
                continue
            awal, akhir_b = blok_objek(body_dasar, k)
            panggil = body_dasar[awal:akhir_b]
            tabel = panggil.startswith("#tabel(")
            chart = "chart_render_" in panggil
            # FIX-GRID-RATA: aturan lama "objek tetap di tempat, teks sebelumnya diregangkan"
            # (FIX-ATURAN-OBJEK2) dihapus: peregangan merusak kesejajaran baris kiri-kanan.
            # Objek yang tidak muat diperkecil (kecil) atau ditunda; sisanya oleh optimasi_skala.
            R = BAWAH - yp
            if kp["jenis"] == "tempat" and tabel and tabel_tinggi(pos, k):
                # FIX-TABEL-GESER: tabel tinggi yang tidak bisa dimulai di sisa halaman penandanya
                # (melompat ke halaman berikutnya, menyisakan ruang kosong) -> posisinya di aliran teks
                # digeser ke paragraf pertama halaman berikutnya: paragraf sesudahnya mengisi ruang itu.
                if ho > hp:
                    ubah = (k, {"jenis": "geser"})
                else:
                    log.append("Tabel {}: tinggi/terpotong -> tetap di tempat (dipotong lintas halaman)".format(k))
                    continue
            if kp["jenis"] == "tempat" and tabel and not ubah and not tabel_tinggi(pos, k):
                # FIX-TABEL-GESER-AMAN: tabel tetap di aliran; geser pemicunya ke paragraf
                # berikut agar teks sesudahnya mengisi halaman. Float "atas" tidak dapat
                # dipotong dan pernah membuat tabel meluber 63 pt ke luar halaman.
                ubah = (k, {"jenis": "geser"})
                log.append("Tabel {} -> geser (tidak muat; teks sesudahnya mengisi halaman)".format(k))
            if ubah and ubah[1]["jenis"] == "geser":
                pass
            elif kp["jenis"] == "tempat" and chart:
                ubah = (k, {"jenis": "tunda"})
            elif kp["jenis"] == "tempat" and not tabel and not chart:
                # kecil: hitung skala gambar agar blok muat di sisa ruang dari penandanya
                m = re.search(r'#gambar\("([^"]+)"', panggil)
                skala = 0.0
                try:
                    from PIL import Image
                    with Image.open(os.path.join(BASE, m.group(1).lstrip("/"))) as im:
                        wpx, hpx = im.size
                    penuh = "penuh: true" in panggil or (
                        "penuh: false" not in panggil and
                        float(re.search(r"lebar-word: ([\d.]+)pt", panggil).group(1)) > LEBAR_KOLOM_PT * 1.25)
                    lebar = 442.2 if penuh else LEBAR_KOLOM_PT
                    
                    # UBAH MENJADI:
                    h_img_asli = lebar * hpx / float(wpx)
                    # Jika gambar selebar halaman menyisakan ruang tanggung (1 s.d. 5 baris),
                    # kecilkan gambar agar sisa ruang di bawahnya bertambah minimal 7 baris (77 pt)
                    ruang_tersisa = 660.0 - (h_img_asli + 33.0)
                    baris_tersisa = math.floor(ruang_tersisa / 11.0)
                    if penuh and 1 <= baris_tersisa <= 5:
                        h_target = 660.0 - (7 * 11.0) - 33.0
                        skala = h_target / h_img_asli
                    else:
                        ruang = math.floor((BAWAH - yp) / 11.0 + 0.001) * 11 - 22
                        skala = (ruang - 11.0) / h_img_asli
                    
                    
                except Exception as e:
                    log.append("Skala objek {} tak terhitung: {}".format(k, e))
                if kp["jenis"] == "tempat" and kolom_obj and skala < 0.7:
                    skala = 0.0
                ubah = (k, {"jenis": "kecil", "skala": skala}) if 0.7 <= skala < 1 else None
                log.append("Objek {}: sisa {:.0f} pt di hlm {}, tinggi {:.0f} pt, "
                           "skala perlu {:.2f}".format(k, R, hp, float(nilai[1]), skala))   # FIX-LOG-KEPUTUSAN
                if ubah is None:
                    ubah = (k, {"jenis": "tunda"})
            elif kp["jenis"] == "kecil":
                ubah = (k, {"jenis": "tunda"})
            elif kp["jenis"] in ("atas", "bawah"):     # FIX-PITA-ATURAN: tak muat di halamannya
                ubah = (k, {"jenis": "tunda"})
            if ubah and ubah[1]["jenis"] in ("tunda", "geser"):
                # probe tanpa objek: paragraf pertama sesudah penanda objek yang mulai di
                # halaman/kolom berikutnya menjadi pemicu float.
                tulis(susun_teks(body_dasar, keputusan, hapus=k))
                pos2, err = kueri_posisi(main)
                target = None
                if pos2:
                    P2 = pos2.get("objek-posisi-{}".format(k))
                    for n in range(sesudah.get(k) or 10 ** 9, 10 ** 6):
                        q = pos2.get("pm-{}".format(n))
                        if q is None:
                            break
                        if kolom_obj:
                            lewat = (q[1], 0 if q[2] < 300 else 1) > (hp, 0 if xp < 300 else 1)
                        else:
                            lewat = q[1] > hp
                        if lewat:
                            target = n
                            break
                if target is None:
                    log.append("Objek {}: tidak ada paragraf untuk penundaan; tetap di tempat".format(k))
                    ubah = None
                    continue
                # FIX-ATAS-SAMA: urutan aturan objek = di tempat -> atas halaman yang sama -> ... -> atas halaman
                # berikutnya. Sebelum ditunda, objek dipicu dari paragraf pertama halaman penandanya (tetap di
                # dalam bagiannya) agar naik ke atas halaman itu; hasilnya diperiksa pada putaran berikutnya.
                if ubah[1]["jenis"] == "tunda" and kp["jenis"] in ("tempat", "kecil") and k not in coba_atas \
                        and pos2 and "target" not in ubah[1]:
                    coba_atas.add(k)
                    h_sbl0 = judul_bagian_sebelum(body_dasar, sesudah.get(k)) or 0
                    awal_h = [int(kk[3:]) for kk, q in pos2.items() if kk.startswith("pm-") and q[1] == hp
                              and h_sbl0 < int(kk[3:]) < (sesudah.get(k) or 0)]
                    if awal_h:
                        min_awal = min(awal_h)
                        def _halangi_maju(k_sbl):
                            kp_sbl = keputusan[k_sbl]
                            I_sbl = pos2.get("objek-info-{}".format(k_sbl))
                            if penuh_obj.get(k) and I_sbl and I_sbl[1] == hp:
                                return True
                            if kp_sbl.get("jenis") in ("tunda", "tempat", "geser"):
                                ref_t = kp_sbl.get("target") or sesudah.get(k_sbl) or 0
                                if ref_t > min_awal:
                                    return True
                            return False
                        if any(_halangi_maju(k_sbl) for k_sbl in keputusan
                               if k_sbl < k and penuh_obj.get(k_sbl) == penuh_obj.get(k)):
                            awal_h = []
                    if awal_h:
                        ubah = (k, {"jenis": "tunda", "target": min(awal_h), "maju": True, "maju_hal": hp,
                                    "lanjut": target})
                        log.append("Objek {}: dicoba ke atas hlm {} (halaman penandanya)".format(k, hp))
                        break
                # FIX-OBJEK-JUDUL: penundaan tidak boleh melewati judul bagian berikutnya (mis. gambar
                # akhir HASIL tidak boleh tampil sesudah judul KESIMPULAN): coba dulu ke atas halaman
                # penandanya; bila sudah, objek dipicu tepat sebelum judul itu.
                h_jdl = judul_bagian_sesudah(body_dasar, sesudah.get(k))
                if h_jdl is not None and target > h_jdl and ubah[1]["jenis"] == "geser":
                    target = h_jdl                  # tabel tetap sebelum judul bagian berikutnya
                elif h_jdl is not None and target > h_jdl and pos2:
                    # dipicu dari paragraf pertama halaman penandanya -> objek naik ke atas halaman itu,
                    # teks sebelum penanda mengalir di bawahnya, judul bagian menyusul sesudah objek
                    # ... tetapi tidak sebelum judul bagian objek itu sendiri (tetap di bagiannya)
                    h_sbl = judul_bagian_sebelum(body_dasar, sesudah.get(k)) or 0
                    awal_hal = [int(kk[3:]) for kk, q in pos2.items() if kk.startswith("pm-") and q[1] == hp
                                and int(kk[3:]) > h_sbl]
                    maju = min(awal_hal) if awal_hal else None
                    if maju is not None and maju < (sesudah.get(k) or 0):
                        target = maju
                        ubah[1]["maju"] = True
                        log.append("Objek {}: ke atas hlm {} (dipicu paragraf {}) agar tidak melewati judul "
                                   "bagian".format(k, hp, maju))
                    else:
                        target = h_jdl
                        log.append("Objek {}: penundaan dibatasi sebelum judul bagian (paragraf {})".format(k, h_jdl))
                ubah[1]["target"] = target
            if ubah:
                break
        if not ubah:
            # FIX-PITA-ATURAN: semua objek sudah benar posisinya -> periksa pita teks < 5 baris.
            # Pita di atas objek -> objek ke atas halaman yang sama; pita di bawah -> ke dasarnya;
            # pita di antara dua objek -> objek atas naik (atau objek bawah turun). Objek yang
            # sudah atas/bawah dan masih melanggar -> ditunda ke atas halaman berikut.
            rc_p, _, _ = jalankan([TYPST, "compile", "--root", BASE, "--font-path",
                                   os.path.join(BASE, "fonts"), main, pdf_pita])
            for pt in (pita_kecil(pdf_pita, pos, penuh_obj) if rc_p == 0 else []):
                if pt["letak"] == "tengah":
                    # FIX-SANDWICH-RULE: teks < 7 baris terjepit di antara Objek k_atas dan k_bawah.
                    # Solusi 1: bila k_atas adalah float (tunda/atas) dan k_bawah adalah objek in-flow
                    # (tempat/geser) serta baris teks >= 3, ubah k_atas menjadi 'geser' ke target k_bawah
                    # sehingga teks naik ke puncak halaman (sebelum k_atas) dan k_atas menempel rapat
                    # dengan k_bawah.
                    k_a, k_b = pt["atas"], pt["bawah"]
                    j_a = keputusan.get(k_a, {}).get("jenis")
                    j_b = keputusan.get(k_b, {}).get("jenis")
                    if (j_a in ("tunda", "atas") and j_b in ("tempat", "geser")
                            and pt["baris"] >= PITA_TEPI_MIN
                            and not body_dasar[slice(*blok_objek(body_dasar, k_a))].startswith("#tabel(")):
                        tgt_b = keputusan.get(k_b, {}).get("target") or sesudah.get(k_b)
                        if tgt_b:
                            kp_baru = dict(keputusan[k_a], jenis="geser", target=tgt_b)
                            # Kompensasi selisih jejak in-flow (+22pt) vs apung (+8pt) agar tinggi blok
                            # objek pertama tetap sama dan tidak mendorong tabel di bawahnya.
                            I_a = pos.get("objek-info-{}".format(k_a))
                            uk_a = ukuran_gambar(body_dasar[slice(*blok_objek(body_dasar, k_a))])
                            if I_a and uk_a:
                                sk_lama = keputusan[k_a].get("skala", 1.0)
                                t_info = float(I_a[0][1]) if isinstance(I_a[0], (list, tuple)) else float(I_a[4] if len(I_a) > 4 else uk_a[1])
                                tetap_a = t_info - uk_a[1] * sk_lama
                                bonus_rapat = 11.0 if body_dasar[slice(*blok_objek(body_dasar, k_b))].startswith("#tabel(") else 0.0
                                jejak_apung = math.ceil((t_info + 8.0) / 11.0 - 0.001) * 11.0 + bonus_rapat
                                for s_int in range(int(round(sk_lama * 100)), 69, -1):
                                    s_coba = s_int / 100.0
                                    jejak_tempat = math.ceil((uk_a[1] * s_coba + tetap_a + 22.0) / 11.0 - 0.001) * 11.0
                                    if jejak_tempat <= jejak_apung:
                                        kp_baru["skala"] = s_coba
                                        break
                            ubah = (k_a, kp_baru)
                            log.append("Objek {}: teks terjepit {} baris di hlm {} dipindah ke atas "
                                       "halaman (sebelum objek) agar rapat dengan Objek {}".format(
                                           k_a, pt["baris"], pt["hal"], k_b))
                            break
                    if j_a == "tunda" and keputusan[k_a].get("maju") and keputusan[k_a].get("lanjut"):
                        h_jdl_a = judul_bagian_sesudah(body_dasar, sesudah.get(k_a))
                        tgt_lanjut = keputusan[k_a]["lanjut"]
                        if h_jdl_a is None or tgt_lanjut <= h_jdl_a:
                            ubah = (k_a, {"jenis": "tunda", "target": tgt_lanjut})
                            log.append("Objek {}: batalkan maju ke atas hlm {} karena menjepit {} baris "
                                       "teks dengan Objek {}".format(k_a, pt["hal"], pt["baris"], k_b))
                            break
                y_mulai_pt = 100.0
                if pt["letak"] == "tengah" and pt.get("atas"):
                    I_atas = pos.get("objek-info-{}".format(pt["atas"]))
                    A_atas = pos.get("objek-akhir-{}".format(pt["atas"]))
                    if A_atas and A_atas[1] == pt["hal"]:
                        y_mulai_pt = A_atas[3]
                    elif I_atas and I_atas[1] == pt["hal"]:
                        tg_atas = float(I_atas[0][1]) if isinstance(I_atas[0], (list, tuple)) and len(I_atas[0]) > 1 else 0.0
                        y_mulai_pt = I_atas[3] + tg_atas
                if pt["letak"] == "total":
                    calon = [(n_o, "tunda") for n_o in reversed(pt["objek"])]
                elif pt["letak"] == "atas":
                    calon = [(pt["bawah"], "atas")]
                elif pt["letak"] == "bawah":
                    calon = [(pt["atas"], "bawah")]
                else:
                    calon = [(pt["atas"], "atas"), (pt["bawah"], "bawah"), (pt["bawah"], "tunda")]
                for k2, arah in calon:
                    j2 = keputusan.get(k2, {}).get("jenis")
                    I2, P2 = pos.get("objek-info-{}".format(k2)), pos.get("objek-posisi-{}".format(k2))
                    A2 = pos.get("objek-akhir-{}".format(k2))
                    if k2 in tabel_panjang or (A2 and I2 and A2[1] != I2[1]):
                        # FIX-PITA-TABEL-ATAS: tabel lintas halaman tidak bisa diapungkan;
                        # pita teks di atasnya dipindah ke segmen sesudah tabel, atau digeser ke halaman berikut.
                        if pt["letak"] in ("atas", "tengah") and j2 in ("tempat", "geser") and I2:
                            info_pita = cari_pita_atas_tabel(pdf_pita, pos, pt["hal"], k2, teks, y_mulai=y_mulai_pt)
                            if info_pita and j2 == "tempat":
                                ubah = (k2, {"jenis": "pindah_pita", "mode": info_pita[0],
                                             "n_pm": info_pita[1], "teks_baris": info_pita[2]})
                                log.append("Objek {}: pita teks {} baris di atas tabel hlm {} "
                                           "dipindah ke bawah tabel".format(k2, pt["baris"], pt["hal"]))
                                break
                        continue                    # FIX-TABEL-PANJANG-INFLOW: jangan diapungkan
                    if not I2 or not P2:
                        continue                                # posisi tidak terbaca
                    h_jdl2 = judul_bagian_sesudah(body_dasar, sesudah.get(k2))
                    if (pt["letak"] in ("atas", "tengah") and j2 == "tempat" and arah == "tunda"
                            and body_dasar[slice(*blok_objek(body_dasar, k2))].startswith("#tabel(")):
                        info_pita = cari_pita_atas_tabel(pdf_pita, pos, pt["hal"], k2, teks, y_mulai=y_mulai_pt)
                        if info_pita:
                            ubah = (k2, {"jenis": "pindah_pita", "mode": info_pita[0],
                                         "n_pm": info_pita[1], "teks_baris": info_pita[2]})
                            log.append("Objek {}: pita teks {} baris di atas tabel hlm {} "
                                       "dipindah ke bawah tabel".format(k2, pt["baris"], pt["hal"]))
                            break
                    if j2 in ("tempat", "kecil") and arah != "tunda":
                        ubah = (k2, {"jenis": arah})
                    elif j2 in ("atas", "bawah") or (arah == "tunda" and j2 in ("tempat", "kecil")):
                        tulis(susun_teks(body_dasar, keputusan, hapus=k2))
                        pos2, _ = kueri_posisi(main)
                        for n in range(sesudah.get(k2) or 10 ** 9, 10 ** 6):
                            q = (pos2 or {}).get("pm-{}".format(n))
                            if q is None:
                                break
                            if q[1] > P2[1]:
                                tgt2 = min(n, h_jdl2) if h_jdl2 is not None else n
                                ubah = (k2, dict(keputusan[k2], jenis="tunda", target=tgt2))
                                break
                    elif j2 == "tunda" and arah == "tunda":
                        # FIX-PITA-TOTAL-MULTI: dua objek tunda yang menumpuk di satu halaman
                        # (pita total) belum tertangani oleh cabang di atas. Kalau target objek
                        # ini sudah berada di halaman SESUDAH halaman objeknya, coba objek lain
                        # di calon; kalau belum, majukan target tunda ke paragraf pertama
                        # halaman sesudah halaman objek sekarang.
                        target_lama = keputusan[k2].get("target")
                        if I2[1] > pt["hal"]:
                            continue
                        tulis(susun_teks(body_dasar, keputusan, hapus=k2))
                        pos2, _ = kueri_posisi(main)
                        for n in range(sesudah.get(k2) or 10 ** 9, 10 ** 6):
                            q = (pos2 or {}).get("pm-{}".format(n))
                            if q is None:
                                break
                            if q[1] > pt["hal"]:
                                tgt2 = min(n, h_jdl2) if h_jdl2 is not None else n
                                if tgt2 == target_lama:
                                    break
                                ubah = (k2, dict(keputusan[k2], jenis="tunda", target=tgt2))
                                break
                    if ubah:
                        log.append("Objek {}: pita teks {} baris ({}) di hlm {}".format(
                            k2, pt["baris"], pt["letak"], pt["hal"]))
                        break
                if ubah:
                    break
            if not ubah and rc_p == 0:
                for lb in lubang_kolom(pdf_pita, pos, penuh_obj, teks):
                    if lb["selisih"] <= 44.0:
                        continue
                    h_lb = lb["hal"]
                    for k_b in sorted(keputusan):
                        if (penuh_obj.get(k_b) and k_b not in tabel_panjang and k_b not in coba_lubang
                                and keputusan[k_b].get("jenis") == "tempat"):
                            I_b = pos.get("objek-info-{}".format(k_b))
                            A_b = pos.get("objek-akhir-{}".format(k_b))
                            if I_b and I_b[1] == h_lb and I_b[3] > 130 and (not A_b or A_b[1] == h_lb):
                                coba_lubang.add(k_b)
                                ubah = (k_b, {"jenis": "bawah"})
                                log.append("Objek {} -> bawah (tutup lubang kolom hlm {}, {:.0f} pt)".format(
                                    k_b, h_lb, lb["selisih"]))
                                break
                    if ubah:
                        break
        if not ubah:
            break
        keputusan[ubah[0]] = ubah[1]
        log.append("Objek {} -> {}".format(ubah[0], ubah[1]["jenis"]))
    # FIX-URUTAN-ANTAROBJEK: objek sejenis (Figure/Table/gambar penuh tanpa label) yang ditunda
    # tidak boleh tampil sesudah objek berikutnya -> jaga urutan pemicu dan posisi tampil.
    def keluarga(k):
        ln = label_dan_nomor(body_dasar, k)
        if ln:
            return ln[0]
        blk = body_dasar[slice(*blok_objek(body_dasar, k))]
        if blk.startswith("#gambar(") and "label: []" in blk and penuh_obj.get(k):
            return "fig_unlabeled"
        return ""

    def nomor(k):
        ln = label_dan_nomor(body_dasar, k)
        if ln:
            return ln[1]
        if keluarga(k) == "fig_unlabeled":
            return k
        return None

    for _ in range(len(keputusan) * 2):
        tulis(susun_teks(body_dasar, keputusan))
        pos, _ = kueri_posisi(main)
        if pos is None:
            break
        ubah = None
        for k1 in sorted(keputusan):
            if keputusan[k1]["jenis"] != "tunda":
                continue
            I1 = pos.get("objek-info-{}".format(k1))
            for k2 in sorted(keputusan):
                if k2 == k1 or not keluarga(k1) or keluarga(k2) != keluarga(k1) \
                        or nomor(k1) is None or nomor(k2) is None or nomor(k2) <= nomor(k1):
                    continue
                I2 = pos.get("objek-info-{}".format(k2))
                if not I1 or not I2:
                    continue
                sama = penuh_obj.get(k1) == penuh_obj.get(k2)
                if (I2[1], I2[3] if sama else 0) < (I1[1], I1[3] if sama else 0):
                    t1 = keputusan[k1].get("target")
                    t2 = keputusan[k2].get("target")
                    if keputusan[k2]["jenis"] == "tunda" and t1 and t2 and t1 > t2:
                        # k1 diberi target lebih besar daripada k2 karena k2 belum dipindah saat k1 di-probe;
                        # tarik k1 ke target k2 agar k1 mendahului k2.
                        ubah = (k1, dict(keputusan[k1], jenis="tunda", target=t2))
                        break
                    elif t2 != t1:
                        ubah = (k2, dict(keputusan[k2], jenis="tunda", target=t1))
                        break
            if ubah:
                break
        if not ubah:
            break
        keputusan[ubah[0]] = ubah[1]
        log.append("Objek {} -> tunda (menjaga urutan sesudah objek sebelumnya)".format(ubah[0]))
    # FIX-PASANGAN-GAMBAR-PADAT & FIX-OBJEK-MELEWATI-JUDUL:
    # Bila dua/lebih gambar penuh ditempatkan pada halaman atau target paragraf yang sama
    # sehingga jejak gabungannya menyisakan terlalu sedikit baris teks (< PITA_TOTAL_MIN + 4 baris
    # per kolom, yang dapat menyebabkan halaman kosong teks saat subjudul lengket atau mendorong
    # objek melewati judul bagian utama berikutnya seperti CONCLUSION), perkecil skalanya bersama
    # (>= 0.70) agar menyisakan minimal PITA_TOTAL_MIN + 4 baris teks (154 pt).
    for _ in range(4):
        tulis(susun_teks(body_dasar, keputusan))
        pos, _ = kueri_posisi(main)
        if pos is None:
            break
        diubah_jdl = False
        batas_ruang = 660.0 - (PITA_TOTAL_MIN + 4) * 11.0
        # Kumpulkan grup gambar penuh yang berbagi halaman tampil ATAU berbagi target tunda
        grup_kandidat = []
        per_hlm = {}
        per_tgt = {}
        for k in sorted(keputusan):
            if keputusan[k].get("jenis") != "tunda":
                continue
            if not penuh_obj.get(k):
                continue
            if not body_dasar[slice(*blok_objek(body_dasar, k))].startswith("#gambar("):
                continue
            I_k = pos.get("objek-info-{}".format(k))
            if I_k:
                per_hlm.setdefault(I_k[1], []).append(k)
            tgt_k = keputusan[k].get("target")
            if tgt_k is not None:
                per_tgt.setdefault((tgt_k, bool(keputusan[k].get("maju"))), []).append(k)
        for g in list(per_hlm.values()) + list(per_tgt.values()):
            if len(g) >= 2 and g not in grup_kandidat:
                grup_kandidat.append(g)
        for k in sorted(keputusan):
            if keputusan[k].get("jenis") != "tunda" or not penuh_obj.get(k):
                continue
            if not body_dasar[slice(*blok_objek(body_dasar, k))].startswith("#gambar("):
                continue
            I_k = pos.get("objek-info-{}".format(k))
            h_jdl = judul_bagian_sesudah(body_dasar, sesudah.get(k))
            t_jdl = pos.get("pm-{}".format(h_jdl)) if h_jdl is not None else None
            if I_k and t_jdl and (I_k[1], I_k[3]) > (t_jdl[1], t_jdl[3]):
                k_prevs = [
                    kp for kp in sorted(keputusan)
                    if kp < k and keputusan[kp].get("jenis") == "tunda" and penuh_obj.get(kp)
                    and body_dasar[slice(*blok_objek(body_dasar, kp))].startswith("#gambar(")
                    and judul_bagian_sesudah(body_dasar, sesudah.get(kp)) == h_jdl
                    and (pos.get("objek-info-{}".format(kp)) or (None, None))[1] == t_jdl[1]
                ]
                if k_prevs:
                    g_jdl = sorted(k_prevs + [k])
                    if g_jdl not in grup_kandidat:
                        grup_kandidat.append(g_jdl)

        for sebagian in grup_kandidat:
            tinggi_asli = []
            jejak_kini = 0.0
            for kk in sebagian:
                uk = ukuran_gambar(body_dasar[slice(*blok_objek(body_dasar, kk))])
                I_kk = pos.get("objek-info-{}".format(kk))
                if uk and I_kk:
                    sk_kini = keputusan[kk].get("skala", 1.0)
                    t_kini = I_kk[4] if len(I_kk) > 4 else (I_kk[0][1] if isinstance(I_kk[0], (list, tuple)) else uk[1] * sk_kini)
                    t_tetap = max(0.0, float(t_kini) - uk[1] * sk_kini)
                    tinggi_asli.append((kk, uk[1], t_tetap))
                    jejak_kini += math.ceil((float(t_kini) + 8.0) / 11.0 - 0.001) * 11.0
            if len(tinggi_asli) != len(sebagian):
                continue
            if jejak_kini <= batas_ruang:
                continue
            skala_pilih = None
            for s_int in range(95, 69, -1):
                s = s_int / 100.0
                total_jejak = sum(math.ceil((h_a * s + t_t + 8.0) / 11.0 - 0.001) * 11.0
                                  for _, h_a, t_t in tinggi_asli)
                if total_jejak <= batas_ruang:
                    skala_pilih = s
                    break
            if skala_pilih is None:
                skala_pilih = 0.75
            for kk, _, _ in tinggi_asli:
                if keputusan[kk].get("skala") != skala_pilih:
                    keputusan[kk] = dict(keputusan[kk], skala=skala_pilih)
                    diubah_jdl = True
            if diubah_jdl:
                log.append("Objek {}: diperkecil ke {:.0%} agar muat bersama minimal {} baris teks/kolom"
                           .format(sebagian, skala_pilih, PITA_TOTAL_MIN + 4))
                break
        if not diubah_jdl:
            break
    tulis(susun_teks(body_dasar, keputusan))
    return keputusan, log


def garis_catatan_kaki(hal):
    """FIX-CATATAN-KAKI: posisi y garis pemisah catatan kaki (garis pendek mendatar di tepi kiri
    area teks, bagian bawah halaman) atau None. Hanya garis 50-150 pt, tanpa garis horizontal
    lain di y yang sama, dan ada teks <= 9,5 pt di bawahnya (bukan garis tabel/footer)."""
    garis = []
    for d in hal.get_drawings():
        r = d["rect"]
        if r.height < 1.0 and r.width > 20:
            garis.append(r)
    for r in garis:
        if not (50 <= r.width <= 150 and abs(r.x0 - 76.5) < 2.0 and 450 < r.y0 < 765):
            continue
        if any(g is not r and g.height < 1.0 and g.width > 20 and abs(g.y0 - r.y0) < 1.0
               for g in garis):
            continue
        kecil = False
        for bl in hal.get_text("dict")["blocks"]:
            for l in bl.get("lines", []):
                sp = [x for x in l["spans"] if x["text"].strip()]
                if sp and r.y0 < sp[0]["origin"][1] < r.y0 + 60 and sp[0]["size"] <= 9.5:
                    kecil = True
                    break
            if kecil:
                break
        if kecil:
            return r.y0
    return None


def catatan_kaki_semua(pdf):
    """FIX-MEMORI-FITZ: posisi garis catatan kaki semua halaman; dokumen dibuka & ditutup sekali."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    hasil = {}
    with fitz.open(pdf) as doc:
        for i, hal in enumerate(doc, 1):
            y = garis_catatan_kaki(hal)
            if y is not None:
                hasil[i] = y
    return hasil


_CACHE_TEKS = {}


def blok_teks(pdf, h):
    """FIX-ANALISIS-CEPAT: blok teks halaman h (mulai 1) tanpa data gambar, dibaca sekali per
    berkas PDF (cache menurut jalur, waktu ubah & ukuran) untuk semua pemeriksaan tata letak."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    st = os.stat(pdf)
    kunci = (pdf, st.st_mtime_ns, st.st_size)
    if kunci not in _CACHE_TEKS:
        _CACHE_TEKS.clear()
        gc.collect()                     # FIX-MEMORI-GC: buang dokumen/cache PDF lama
        flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
        with fitz.open(pdf) as doc:
            _CACHE_TEKS[kunci] = [p.get_text("dict", flags=flags)["blocks"] for p in doc]
    return _CACHE_TEKS[kunci][h - 1]


_CACHE_TERLIHAT = {}


def baris_teks_terlihat(pdf, h):
    """Baris dari glyph yang dicat PDF; origin span adalah baseline, bukan kotak layout."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    st = os.stat(pdf)
    key = (os.path.abspath(pdf), st.st_mtime_ns, st.st_size)
    if key not in _CACHE_TERLIHAT:
        _CACHE_TERLIHAT.clear()
        pages = []
        with fitz.open(pdf) as doc:
            for page in doc:
                rows = {}
                crop = page.rect
                for trace in page.get_texttrace():
                    # PDF rendering mode 3 = invisible; opacity 0 juga tidak dicat.
                    if trace["type"] not in (0, 1) or trace["opacity"] <= 0:
                        continue
                    groups = {}
                    for code, _, origin, bbox in trace["chars"]:
                        if not 0 <= code <= 0x10ffff or not all(
                                math.isfinite(v) for v in (*origin, *bbox)):
                            continue
                        if (bbox[2] <= bbox[0] or bbox[3] <= bbox[1]
                                or bbox[2] <= crop.x0 or bbox[0] >= crop.x1
                                or bbox[3] <= crop.y0 or bbox[1] >= crop.y1):
                            continue
                        groups.setdefault(round(origin[1], 3), []).append(
                            (chr(code), origin, bbox))
                    for chars in groups.values():
                        ink = [c for c in chars if c[0].isprintable() and not c[0].isspace()]
                        if not ink:
                            continue
                        # Asal span menentukan kolom. Ujung kanan kata bisa melampaui 300 pt.
                        x, y = ink[0][1]
                        kol = 0 if x < 300 else 1
                        bbox = (min(c[2][0] for c in ink), min(c[2][1] for c in ink),
                                max(c[2][2] for c in ink), max(c[2][3] for c in ink))
                        span = dict(text="".join(c[0] for c in chars), origin=(x, y),
                                    size=trace["size"], font=trace["font"], bbox=bbox)
                        rows.setdefault((kol, round(y, 3)), []).append(span)
                lines = []
                for (kol, _), spans in sorted(rows.items()):
                    spans.sort(key=lambda s: s["origin"][0])
                    bbox = (min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
                            max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans))
                    lines.append(dict(kol=kol, bbox=bbox, spans=spans))
                pages.append(lines)
        _CACHE_TERLIHAT[key] = pages
    return _CACHE_TERLIHAT[key][h - 1]


def baris_narasi_terlihat(pdf, h):
    """Teks isi 11 pt, termasuk yang sudah melewati footer; jangan disembunyikan batas y."""
    hasil = []
    for line in baris_teks_terlihat(pdf, h):
        spans = [s for s in line["spans"] if abs(s["size"] - 11) <= 0.2
                 and s["origin"][1] > 102.4]
        if not spans:
            continue
        bbox = (min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
                max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans))
        hasil.append(dict(kol=line["kol"], bbox=bbox, spans=spans))
    return hasil


def baseline_target_sama(pasangan, delta):
    """Izin footer harus dibuktikan oleh dua baseline teks, bukan ukuran objek."""
    return (len(pasangan) == 2 and {x["kol"] for x in pasangan} == {0, 1}
            and all(not x["kosong"] and x.get("baris_terlihat", 0) > 0
                    and x.get("akhir_baseline") is not None
                    and math.isfinite(x["akhir_baseline"])
                    and abs(x["target"] - x["akhir_baseline"] - delta) <= ALIGN_EPS
                    and abs(x["kurang"] - delta) <= ALIGN_EPS for x in pasangan)
            and abs(pasangan[0]["akhir_baseline"] - pasangan[1]["akhir_baseline"]) <= ALIGN_EPS)


PITA_TOTAL_MIN = 10          # FIX-PITA-TOTAL: min. baris teks per kolom di halaman berobjek
PITA_TEPI_MIN = 5            # FIX-PITA-MIN5: min. 5 baris (55 pt) teks di atas objek pertama / di bawah objek terakhir
PITA_TENGAH_MIN = 7          # FIX-SANDWICH-RULE: min. 7 baris (77 pt) teks terjepit di antara dua objek


def pita_kecil(pdf, pos, penuh, teks=""):
    """FIX-PITA-UKUR & FIX-SANDWICH-RULE: pita teks di tepi halaman (< 3 baris) atau terjepit
    di antara dua objek (< 7 baris / 77 pt). Hasil: daftar dict(hal, letak
    'atas'|'tengah'|'bawah', baris, atas=nomor objek di atas pita, bawah=nomor objek di bawahnya)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    ATAS, BAWAH = 102.4, 841.89 - 79.0
    jml_hal = _jumlah_halaman(pdf)
    obj = {}
    bersambung = set()       # FIX-TABEL-GESER: halaman awal tabel yang berlanjut ke halaman berikutnya
    for k, v in pos.items():
        m = re.match(r"objek-info-(\d+)$", k)
        if not m or not penuh.get(int(m.group(1)), False):
            continue
        nomor_obj = int(m.group(1))
        if teks and objek_frozen_teks(teks, nomor_obj):
            continue
        n, hal, y0 = nomor_obj, v[1], v[3]
        tinggi = float(v[0][1]) if isinstance(v[0], (list, tuple)) else 0.0
        akhir = pos.get("objek-akhir-{}".format(n))
        y1 = (akhir[3] if akhir[1] == hal else BAWAH) if akhir else y0 + tinggi
        obj.setdefault(hal, []).append((y0, y1, n))
        if akhir and akhir[1] > hal:                 # tabel bersambung ke halaman berikut
            bersambung.add(hal)
            for h in range(hal + 1, akhir[1] + 1):
                obj.setdefault(h, []).append((ATAS, akhir[3] if h == akhir[1] else BAWAH, n))
    def baris(h, kol):
        return [round(l["spans"][0]["origin"][1], 1) for bl in blok_teks(pdf, h)
                for l in bl.get("lines", []) if "".join(s["text"] for s in l["spans"]).strip()
                and abs(l["spans"][0]["size"] - 11) < 0.2
                and ((l["bbox"][2] < 300) if kol == 0 else (l["bbox"][0] >= 290))
                and abs(((l["spans"][0]["origin"][1] - 111.2) % 11 + 5.5) % 11 - 5.5) < 0.3]
    hasil = []
    for hal in sorted(obj):
        if hal > jml_hal:
            continue
        kot = sorted(obj[hal])
        batas = [(ATAS, None)] + [x for o in kot for x in ((o[0], o[2]), (o[1], o[2]))] + [(BAWAH, None)]
        L = [set(baris(hal, 0)), set(baris(hal, 1))]
        total = [0, 0]
        for i in range(0, len(batas), 2):
            (a, n_atas), (z, n_bawah) = batas[i], batas[i + 1]
            for c in (0, 1):
                total[c] += sum(1 for y in L[c] if a < y < z + 1)
            n = max(sum(1 for y in L[c] if a < y < z + 1) for c in (0, 1))
            letak = "atas" if n_atas is None else ("bawah" if n_bawah is None else "tengah")
            if hal == 1 and letak == "atas":
                continue
            # FIX-PITA-MIN3 & FIX-SANDWICH-RULE: tepi halaman min. 3 baris; di antara dua objek min. 7 baris.
            batas_min = PITA_TENGAH_MIN if letak == "tengah" else PITA_TEPI_MIN
            if 0 < n < batas_min:
                hasil.append(dict(hal=hal, letak=letak, baris=n, atas=n_atas, bawah=n_bawah))
        # FIX-PITA-TOTAL: halaman yang memuat objek selebar halaman harus berisi minimal
        # PITA_TOTAL_MIN baris teks per kolom (kecuali tanpa teks sama sekali); bila tidak, teksnya
        # tampak seperti "sisa" di sekitar objek yang hampir sepenuh halaman.
        if 0 < max(total) < PITA_TOTAL_MIN and hal < jml_hal and hal not in bersambung:
            hasil.append(dict(hal=hal, letak="total", baris=max(total), atas=None, bawah=None,
                              objek=sorted(set(o[2] for o in kot))))
    return hasil


REF_PM_AWAL = [None]    # FIX-REF-TUNGGAL3: nomor penanda entri referensi pertama


def baris_tunggal(pdf, pos, teks=""):
    """FIX-TUNGGAL-UKUR: baris tunggal paragraf narasi: widow (baris terakhir paragraf sendirian di
    puncak kolom) dan orphan (baris pertama paragraf sendirian di dasar kolom). Baris judul (tebal)
    tidak dihitung. FIX-BUTIR-TUNGGAL: #butir satu baris di dasar kolom (y > 740) juga dideteksi.
    Hasil: daftar dict(jenis, hal, kol, y=baseline)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    jml_hal = _jumlah_halaman(pdf)
    awal = set()
    pm_awal = {}
    for k, v in pos.items():
        if k.startswith("pm-"):
            # penanda paragraf narasi ada di atas baris (garis dasar - 8,8 pt); penanda entri
            # referensi (paragraf gantung) tercatat di garis dasar -> keduanya diterima
            n = int(k[3:])
            for yy in (round(v[3] + 8.8), round(v[3])):
                kunci = (v[1], 0 if v[2] < 300 else 1, yy)
                awal.add(kunci)
                pm_awal[kunci] = n
    def mulai(h, c, y):
        return any((h, c, round(y) + d) in awal for d in (-1, 0, 1))
    def pm_mulai(h, c, y):
        for d in (-1, 0, 1):
            n = pm_awal.get((h, c, round(y) + d))
            if n is not None:
                return n
        return None
    butir_pm = set(int(m.group(1)) for m in re.finditer(
        r"#butir\([^)]*\)\[#pm\((\d+)\);", teks or ""))
    pm_beku = pm_beku_teks(teks)
    rentang_beku = []
    for n in pm_beku:
        v = pos.get("pm-{}".format(n))
        if not v:
            continue
        h_pm = v[1]
        kol_pm = 0 if v[2] < 300 else 1
        y_a = v[3] - 2.0
        ve = pos.get("frag-akhir-{}-0".format(n))
        y_b = (ve[3] + 13.0) if ve else (y_a + 50.0)
        rentang_beku.append((h_pm, kol_pm, y_a, y_b))
    # FIX-TUNGGAL-OBJEK: baris di dalam objek (tabel) bukan narasi
    area = area_objek_ketat(pos, teks)
    region = []
    for h in range(1, jml_hal + 1):
        for c in (0, 1):
            ys, rumus_y = {}, []
            for l in baris_narasi_terlihat(pdf, h):
                sp = [x for x in l["spans"] if x["text"].strip()]
                if not sp or abs(sp[0]["size"] - 11) > 0.2 or sp[0]["origin"][1] <= 102.4:
                    continue
                if l["kol"] != c:
                    continue
                y = round(sp[0]["origin"][1], 1)
                if any(rh == h and (rkol == c or rkol == -1) and ya <= y <= yb for rh, rkol, ya, yb in rentang_beku):
                    continue
                if all("math" in x["font"].lower() for x in sp) or \
                        RE_NOMOR_RUMUS.match("".join(x["text"] for x in sp).strip()):
                    rumus_y.append(y)        # FIX-TUNGGAL-RUMUS: isi/nomor rumus display, bukan baris teks
                    continue
                if abs(((y - 111.2) % 11 + 5.5) % 11 - 5.5) > 0.3:
                    continue
                if any(a - 1 < y < z + 1 for a, z in area.get((h, c), [])):
                    continue
                tebal = all("Bold" in x["font"] for x in sp)
                ys[y] = ys.get(y, True) and tebal
            run = []
            for y in sorted(ys):
                # celah karena rumus display bukan batas kolom (FIX-TUNGGAL-RUMUS)
                if run and y - run[-1][0] > 11.6 and not any(run[-1][0] < m < y for m in rumus_y):
                    region.append((h, c, run))
                    run = []
                run.append((y, ys[y]))
            if run:
                region.append((h, c, run))
    hasil = []
    for i, (h, c, run) in enumerate(region):
        (y0, b0) = run[0]
        if not b0 and not mulai(h, c, y0) and (len(run) == 1 or mulai(h, c, run[1][0])):
            hasil.append(dict(jenis="widow", hal=h, kol=c, y=y0))
        (y1, b1) = run[-1]
        # FIX-JUDUL-DASAR: judul (baris tebal) sebagai baris terakhir kolom, isinya di kolom berikut
        if b1 and i + 1 < len(region) and y1 > 700:
            hasil.append(dict(jenis="judul", hal=h, kol=c, y=y1))
        # FIX-BUTIR-TUNGGAL: #butir satu baris yang terdampar di dasar kolom (y > 740).
        if len(run) == 1 and not b1 and y1 > 740 and i + 1 < len(region):
            pm = pm_mulai(h, c, y1)
            if pm is not None and pm in butir_pm:
                hasil.append(dict(jenis="butir", hal=h, kol=c, y=y1, pm=pm))
            elif mulai(h, c, y1):
                h2, c2, run2 = region[i + 1]
                if not mulai(h2, c2, run2[0][0]) and not run2[0][1]:
                    hasil.append(dict(jenis="orphan", hal=h, kol=c, y=y1))
        if len(run) >= 2 and not b1 and mulai(h, c, y1) and i + 1 < len(region):
            h2, c2, run2 = region[i + 1]
            if not mulai(h2, c2, run2[0][0]) and not run2[0][1]:
                hasil.append(dict(jenis="orphan", hal=h, kol=c, y=y1))
    # Referensi tetap ditandai untuk diagnostik, tanpa pengecualian widow/orphan.
    awal_ref = pos.get("pm-{}".format(REF_PM_AWAL[0])) if REF_PM_AWAL[0] else None
    if awal_ref:
        batas = (awal_ref[1], 0 if awal_ref[2] < 300 else 1, awal_ref[3] - 2)
        for t in hasil:
            t["ref"] = (t["hal"], t["kol"], t["y"]) >= batas
        # FIX-REF-DUA-BARIS: entri yang terpotong min. 2 baris di kedua sisi (kiri & kanan)
    return hasil


def area_objek(pos):
    """Daerah vertikal objek per halaman {hal: [(y_atas, y_bawah)]} (tabel lintas halaman ikut)."""
    area = {}
    for k, v in pos.items():
        m = re.match(r"objek-info-(\d+)$", k)
        if not m:
            continue
        tinggi = float(v[0][1]) if isinstance(v[0], (list, tuple)) else 0.0
        akhir = pos.get("objek-akhir-{}".format(m.group(1)))
        if akhir:
            for h in range(v[1], akhir[1] + 1):
                y_a, y_z = (v[3] if h == v[1] else 0), (akhir[3] if h == akhir[1] else 900)
                if y_z < y_a:        # tabel satu kolom berlanjut ke kolom kanan halaman yang sama
                    area.setdefault(h, []).extend([(y_a, 900), (0, y_z)])
                else:
                    area.setdefault(h, []).append((y_a, y_z))
        else:
            area.setdefault(v[1], []).append((v[3], v[3] + tinggi))
    return area


def area_objek_ketat(pos, teks):
    """Area per kolom: objek satu kolom tidak menyembunyikan teks kolom sebelahnya."""
    area = {}
    for label, v in pos.items():
        m = re.fullmatch(r"objek-info-(\d+)", label)
        if not m:
            continue
        nomor_obj = int(m[1])
        if objek_frozen_teks(teks, nomor_obj):
            continue
        penuh = objek_penuh_teks(teks, nomor_obj)
        akhir = pos.get("objek-akhir-" + m[1])
        tinggi = float(v[0][1]) if isinstance(v[0], (tuple, list)) else 0.0
        awal_pos = (v[1], 0 if v[2] < 300 else 1)
        akhir_pos = ((akhir[1], 0 if akhir[2] < 300 else 1) if akhir else awal_pos)
        for h in range(v[1], akhir[1] + 1 if akhir else v[1] + 1):
            for kol in (0, 1):
                if not penuh and not awal_pos <= (h, kol) <= akhir_pos:
                    continue
                a = v[3] if h == v[1] and (penuh or kol == awal_pos[1]) else 102.4
                z = (akhir[3] if akhir and h == akhir[1] and (penuh or kol == akhir_pos[1])
                     else v[3] + tinggi if not akhir else 762.4)
                area.setdefault((h, kol), []).append((a, z))
    return area


def baris_luar_grid(pdf, pos, teks):
    """FIX-GRID-QA: baris narasi 11 pt yang baseline-nya tidak di grid 111,2 + 11n pt, sehingga
    tidak sejajar dengan baris kolom sebelahnya. Halaman 1 diukur mulai baris narasi pertama.
    Hasil: daftar dict(hal, kol, baris=jumlah, y=baseline pertama)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    jml_hal = _jumlah_halaman(pdf)
    area = area_objek_ketat(pos, teks)
    pm_beku = pm_beku_teks(teks)
    rentang_beku = []
    for n in pm_beku:
        v = pos.get("pm-{}".format(n))
        if not v:
            continue
        h_pm = v[1]
        kol_pm = 0 if v[2] < 300 else 1
        y_a = v[3] - 2.0
        ve = pos.get("frag-akhir-{}-0".format(n))
        y_b = (ve[3] + 13.0) if ve else (y_a + 50.0)
        rentang_beku.append((h_pm, kol_pm, y_a, y_b))
    for lbl, v in pos.items():
        mo = re.fullmatch(r"objek-info-(\d+)", lbl)
        if not mo or not objek_frozen_teks(teks, int(mo[1])):
            continue
        nomor = int(mo[1])
        h_ob = v[1]
        kol_ob = 0 if v[2] < 300 else 1
        y_a = v[3] - 2.0
        ak = pos.get("objek-akhir-{}".format(nomor))
        tg = float(v[0][1]) if isinstance(v[0], (tuple, list)) and len(v[0]) > 1 else 0.0
        y_b = ak[3] + 2.0 if ak else (v[3] + tg + 2.0)
        rentang_beku.append((h_ob, kol_ob, y_a, y_b))
    # halaman 1: grid dimulai dari baris narasi pertama (blok judul menggeser fasenya, tetapi
    # kedua kolom tetap harus sejajar satu sama lain)
    pm1 = [v[3] + 8.8 for k, v in pos.items() if k.startswith("pm-") and v[1] == 1]
    mulai1 = min(pm1) - 0.5 if pm1 else 900
    fase = {1: (min(pm1) if pm1 else 111.2)}
    hasil = {}
    for h in range(1, jml_hal + 1):
        for l in baris_narasi_terlihat(pdf, h):
            sp = [x for x in l["spans"] if x["text"].strip()]
            if not sp or abs(sp[0]["size"] - 11) > 0.2 or sp[0]["origin"][1] <= 102.4:
                continue
            if all("math" in x["font"].lower() for x in sp):
                continue                     # isi rumus display, bukan baris teks
            if RE_NOMOR_RUMUS.match("".join(x["text"] for x in sp).strip()):
                continue                     # FIX-RUMUS-BERNOMOR: nomor rumus di tengah tinggi rumus
            y = sp[0]["origin"][1]
            kol_area = l["kol"]
            if any(rh == h and (rkol == kol_area or rkol == -1) and ya <= y <= yb for rh, rkol, ya, yb in rentang_beku):
                continue
            if (h == 1 and y < mulai1) or any(
                    a - 0.1 < y < z + 0.1 for a, z in area.get((h, kol_area), [])):
                continue
            if abs(((y - fase.get(h, 111.2)) % 11 + 5.5) % 11 - 5.5) <= ALIGN_EPS:
                continue
            kol = l["kol"]
            if kol < 0:
                continue
            d = hasil.setdefault((h, kol), dict(hal=h, kol=kol, baris=0, y=y))
            d["baris"] += 1
            d["y"] = min(d["y"], y)
    return [hasil[k] for k in sorted(hasil)]



TARGET_ARTIKEL = "549-3461-1-LE"
TARGET_HALAMAN = (581, 595)


def footer_target(pdf, teks):
    """Pengecualian khusus artikel 549 atau pergeseran umum 11 pt non-akhir."""
    directives = re.findall(r"^#kaki-naik\((\d+), ([\d.]+)pt\)\s*$", teks, re.M)
    if not directives:
        return {}, (["Direktif footer tidak sah"] if "#kaki-naik" in teks else [])
    try:
        with open(os.path.join(os.path.dirname(pdf), "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        awal = int(meta["terbitan"]["halaman_awal"])
    except (OSError, ValueError, KeyError):
        return {}, ["Metadata izin footer tidak tersedia"]
    hasil, masalah = {}, []
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    with fitz.open(pdf) as doc:
        for h_s, d_s in directives:
            h, d = int(h_s), float(d_s)
            khusus = (meta.get("_artikel_id") == TARGET_ARTIKEL
                      and awal + h - 1 in TARGET_HALAMAN
                      and d in (11.0, 22.0)
                      and 1 <= h <= len(doc))
            umum = (d in (11.0, 22.0) and 1 <= h < len(doc))
            if not (khusus or umum) or h in hasil:
                masalah.append("Izin footer tidak sah di hlm fisik {}".format(h))
                continue
            nyata = any(abs(x["rect"].y0 - (765.5 - d)) <= ALIGN_EPS
                        and abs(x["rect"].width - 442.2) <= 1
                        for x in doc[h - 1].get_drawings())
            if not nyata:
                masalah.append("Footer hlm fisik {} tidak sesuai direktif".format(h))
                continue
            hasil[h] = d
    return hasil, masalah


def sisipkan_kaki_naik(teks, u):
    """Sisipkan #kaki-naik(h, 11.0pt) bila kedua kolom hlm non-akhir kurang 11 pt dan sejajar."""
    if not u or not u.get("halaman"):
        return teks, []
    kepala = '#import "/template_jurnal.typ": *\n'
    if kepala not in teks:
        return teks, []
    daftar_h = []
    teks_baru = teks
    for h in range(1, u["halaman"]):
        xs = [x for x in u.get("kolom", []) if x.get("hal") == h]
        if len(xs) != 2:
            continue
        xs = sorted(xs, key=lambda x: x.get("kol", 0))
        kiri, kanan = xs[0], xs[1]
        for target_d in (11.0, 22.0):
            if (not kiri.get("kosong") and not kanan.get("kosong")
                    and not kiri.get("penuh_objek") and not kanan.get("penuh_objek")
                    and abs(kiri.get("kurang", 0.0) - target_d) <= ALIGN_EPS
                    and abs(kanan.get("kurang", 0.0) - target_d) <= ALIGN_EPS
                    and kiri.get("akhir_baseline") is not None
                    and kanan.get("akhir_baseline") is not None
                    and abs(kiri["akhir_baseline"] - kanan["akhir_baseline"]) <= ALIGN_EPS):
                pola = r"^#kaki-naik\({},".format(h)
                if not re.search(pola, teks_baru, flags=re.M):
                    sisipan = "#kaki-naik({}, {:.1f}pt)\n".format(h, target_d)
                    teks_baru = teks_baru.replace(kepala, kepala + sisipan, 1)
                    daftar_h.append(h)
                break
    return teks_baru, daftar_h


def tandai_pengecualian_target(kolom, footer):
    """Sisa fisik tetap dicatat; pengecualian hanya jika kedua sisi tepat sama."""
    for h, delta in footer.items():
        pasangan = [x for x in kolom if x["hal"] == h]
        sah = baseline_target_sama(pasangan, delta)
        for x in pasangan:
            x["footer_naik"] = delta
            x["pengecualian_target"] = sah
            x["sisa_ke_footer"] = x["kurang"] - delta
    return kolom




BATAS_KANAN_AKHIR_PT = 200.0
# PROJECT_CONTEXT §5.3: kolom kanan wajib diupayakan 0 pt; toleransi maksimal 1 baris (11 pt).
# Sisa 22 pt (2 baris) dilarang keras. Kolom kiri tetap wajib 0 pt pada semua halaman.
BATAS_KANAN_NONAKHIR_PT = 11.0
TOLERANSI_OBJEK_PENUH_PT = 22.0


def halaman_akhir_referensi(pos, teks):
    """Hanya ujung References yang sebenarnya; halaman di tengahnya tetap wajib penuh."""
    entri = list(re.finditer(r"#referensi\[#pm\((\d+)\)", teks))
    if not entri:
        return set()
    nomor = int(entri[-1][1])
    akhir = [v for label, v in pos.items()
             if re.fullmatch(r"frag-akhir-{}-\d+".format(nomor), label)]
    if not akhir:
        return set()
    v = max(akhir, key=lambda v: (v[1], 0 if v[2] < 300 else 1, v[3]))
    return {v[1]} if v[2] >= 290 else set()


def batas_kanan(hal, jumlah, akhir_referensi=()):
    return BATAS_KANAN_AKHIR_PT if hal == jumlah else BATAS_KANAN_NONAKHIR_PT


def tandai_batas_akhir(kolom, pos, teks, jumlah):
    akhir_ref = halaman_akhir_referensi(pos, teks)
    for x in kolom:
        x["batas_kanan_pt"] = batas_kanan(x["hal"], jumlah, akhir_ref) if x["kol"] == 1 else ALIGN_EPS
        x["izin_akhir"] = ("akhir_dokumen" if x["hal"] == jumlah else
                           None) if x["kol"] == 1 else None
    return kolom


def kolom_sah(x, jumlah):
    gap = x["kurang"]
    if x.get("kosong") or not math.isfinite(gap):
        return False
    if x.get("segmen_objek"):
        return abs(gap) <= TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS
    if x.get("akhir_seimbang") and x["hal"] == jumlah:
        # Halaman terakhir yang seimbang: sisa ruang dibagi dua kolom karena naskah habis.
        return gap >= -ALIGN_EPS and x.get("baris_terlihat", 0) > 0
    if x.get("pengecualian_target"):
        return (x.get("baris_terlihat", 0) > 0 and x.get("akhir_baseline") is not None
                and abs(x["target"] - x["akhir_baseline"] - x["footer_naik"]) <= ALIGN_EPS)
    batas = x.get("batas_kanan_pt", batas_kanan(x["hal"], jumlah)) if x["kol"] == 1 else ALIGN_EPS
    return -ALIGN_EPS <= gap <= batas + ALIGN_EPS


def pesan_kolom(x, jumlah, halaman_awal=None):
    halaman = "hlm fisik {}".format(x["hal"]) if halaman_awal is None else "hlm {}".format(halaman_awal + x["hal"] - 1)
    batas = x.get("batas_kanan_pt", batas_kanan(x["hal"], jumlah)) if x["kol"] == 1 else ALIGN_EPS
    aturan = "maks. {:.0f} pt".format(batas) if batas > ALIGN_EPS else "target 0 pt"
    if x.get("kosong"):
        return "Kolom {} {}: tidak ada baseline teks terlihat".format(
            "kiri" if x["kol"] == 0 else "kanan", halaman)
    return "Kolom {} {}: sisa {:.2f} pt ({})".format("kiri" if x["kol"] == 0 else "kanan", halaman, x["kurang"], aturan)


def ukur_kolom(pdf, pos, teks, info=None):
    """Ukur HANYA baseline teks terlihat. Kotak objek/padding tidak mengisi kolom."""
    jumlah = _jumlah_halaman(pdf)
    if not pos or not any(k.startswith("pm-") for k in pos):
        raise ValueError("Metadata paragraf tidak lengkap")
    kaki = catatan_kaki_semua(pdf)
    pertama = pos.get("pm-1")
    fase1 = pertama[3] + 8.8 if pertama and pertama[1] == 1 else 111.2
    target_halaman = {}
    for h in range(1, jumlah + 1):
        fase = fase1 if h == 1 else 111.2
        batas_fisik = min(762.4, kaki[h] - 12.8) if h in kaki else 762.4
        target_halaman[h] = fase + math.floor(
            (batas_fisik - 2.2 - fase + 0.001) / 11.0) * 11.0
    # FIX-UKUR-PENUH-BAWAH: objek selebar halaman yang menyentuh dasar area teks menutup
    # KEDUA kolom pada halaman itu (tabel lanjut, tabel/gambar di dasar). Baseline teks
    # terakhir di atasnya bukan ujung kolom; ujung kolom adalah objek yang sama untuk
    # kiri dan kanan, sehingga kedua kolom sejajar penuh. Isi sel tabel tetap tidak
    # dihitung sebagai baseline narasi.
    tutup = set()
    bawah_satu_kol = {}
    for label, v in pos.items():
        m = re.fullmatch(r"objek-info-(\d+)", label)
        if not m:
            continue
        nomor = int(m[1])
        m_obj = re.search(r"#(?:gambar|tabel)\([^\n]*\bnomor: {}\b[^\n]*".format(nomor), teks)
        if not m_obj or objek_frozen_teks(teks, nomor):
            continue
        panggil_obj = m_obj.group(0)
        tinggi = float(v[0][1]) if isinstance(v[0], (tuple, list)) and len(v[0]) > 1 else 0.0
        akhir = pos.get("objek-akhir-{}".format(nomor))
        if akhir:
            h1, y1 = akhir[1], akhir[3]
        else:
            h1, y1 = v[1], v[3] + tinggi
        if objek_penuh(panggil_obj):
            for h in range(v[1], h1 + 1):
                if v[3] > target_halaman[h] + ALIGN_EPS:
                    continue
                # FIX-UKUR-OBJEK-TOLERANSI: objek selebar halaman yang berakhir dalam batas
                # kelonggaran kolom kanan (maks. 2 baris) tetap dianggap menutup kedua kolom.
                # Halaman gambar selebar halaman (mis. Figure 1+2) tidak lagi dilaporkan kosong
                # hanya karena captionnya 9 pt dan baseline 11 pt terakhir berada di atas objek.
                if h < h1 or y1 >= target_halaman[h] - 33.0 - ALIGN_EPS:
                    tutup.add((h, 0))
                    tutup.add((h, 1))
        else:
            kol_o = 0 if v[2] < 300 else 1
            for h in range(v[1], h1 + 1):
                if h not in target_halaman:
                    continue
                if (h > v[1] or v[3] <= target_halaman[h] + ALIGN_EPS) and (
                        h < h1 or y1 >= target_halaman[h] - 33.0 - ALIGN_EPS):
                    y0_h = v[3] if h == v[1] else 102.4
                    fase_h = fase1 if h == 1 else 111.2
                    if y0_h <= fase_h + 11.0 + ALIGN_EPS:
                        tutup.add((h, kol_o))
                    else:
                        if 'keputusan: "bawah"' in panggil_obj:
                            t_r = tinggi + 4.0
                            clr = math.ceil((t_r + 8.0) / 11.0 - 0.001) * 11.0 - t_r
                        elif panggil_obj.startswith("#gambar(") and tinggi > 0:
                            norm_h = math.ceil((tinggi + 22.0) / 11.0 - 0.001) * 11.0
                            clr = (norm_h - tinggi) / 2.0
                        else:
                            clr = 11.0
                        prev = bawah_satu_kol.get((h, kol_o))
                        if prev is None or y0_h < prev[0]:
                            bawah_satu_kol[(h, kol_o)] = (y0_h, clr)
    rentang_beku_semua = []
    for n_pm in pm_beku_teks(teks):
        v_pm = pos.get("pm-{}".format(n_pm))
        if not v_pm:
            continue
        h_pm = v_pm[1]
        kol_pm = 0 if v_pm[2] < 300 else 1
        y_a = v_pm[3] - 2.0
        ve_pm = pos.get("frag-akhir-{}-0".format(n_pm))
        y_b = (ve_pm[3] + 13.0) if ve_pm else (y_a + 50.0)
        rentang_beku_semua.append((h_pm, kol_pm, y_a, y_b))
    for lbl, v in pos.items():
        mo = re.fullmatch(r"objek-info-(\d+)", lbl)
        if not mo or not objek_frozen_teks(teks, int(mo[1])):
            continue
        nomor = int(mo[1])
        h_ob = v[1]
        kol_ob = 0 if v[2] < 300 else 1
        y_a = v[3] - 2.0
        ak = pos.get("objek-akhir-{}".format(nomor))
        tg = float(v[0][1]) if isinstance(v[0], (tuple, list)) and len(v[0]) > 1 else 0.0
        y_b = ak[3] + 2.0 if ak else (v[3] + tg + 2.0)
        rentang_beku_semua.append((h_ob, kol_ob, y_a, y_b))
    hasil = []
    for h in range(1, jumlah + 1):
        fase = fase1 if h == 1 else 111.2
        target = target_halaman[h]
        isi = {0: [], 1: []}
        for line in baris_narasi_terlihat(pdf, h):
            kol = line["kol"]
            for span in line["spans"]:
                y = span["origin"][1]
                if h == 1 and y < fase - ALIGN_EPS:
                    continue
                is_offgrid = abs(((y - fase) % 11 + 5.5) % 11 - 5.5) > ALIGN_EPS
                if is_offgrid and any(rh == h and (rkol == kol or rkol == -1) and ya <= y <= yb for rh, rkol, ya, yb in rentang_beku_semua):
                    continue
                # Jangan memotong pengukuran di footer/batas_fisik: teks yang menabrak
                # footer harus tetap terukur lalu ditahan, bukan hilang dari QA.
                isi[kol].append(y)
        timpang = {}
        obj_penuh_h = []
        for label, v in pos.items():
            m_p = re.fullmatch(r"objek-info-(\d+)", label)
            if not m_p or not objek_penuh_teks(teks, int(m_p[1])):
                continue
            nomor_p = int(m_p[1])
            if objek_frozen_teks(teks, nomor_p):
                continue
            if v[1] == h and v[3] > 102.4 + 5:
                obj_penuh_h.append((v[3], nomor_p, v))
        obj_penuh_h.sort(key=lambda item: item[0])

        batas_atas = 0.0
        for y0, nomor, v in obj_penuh_h:
            rentang_lain = {0: [], 1: []}
            obj_seg = {0: [], 1: []}
            obj_atas = {0: [], 1: []}
            for lbl, vo in pos.items():
                mo = re.fullmatch(r"objek-info-(\d+)", lbl)
                if not mo or int(mo[1]) == nomor:
                    continue
                no = int(mo[1])
                ak = pos.get("objek-akhir-{}".format(no))
                if ak:
                    h_start, y_start = vo[1], vo[3]
                    h_end, y_end = ak[1], ak[3]
                else:
                    tg = float(vo[0][1]) if isinstance(vo[0], (tuple, list)) else 0.0
                    h_start, y_start = vo[1], vo[3]
                    h_end, y_end = vo[1], vo[3] + tg
                if h_start <= h <= h_end:
                    y_a = y_start if h_start == h else 0.0
                    y_b = y_end if h_end == h else target
                    if objek_penuh_teks(teks, no):
                        rentang_lain[0].append((y_a, y_b))
                        rentang_lain[1].append((y_a, y_b))
                    else:
                        kol_vo = 0 if vo[2] < 300 else 1
                        rentang_lain[kol_vo].append((y_a, y_b))
                        if (not objek_frozen_teks(teks, no)
                                and batas_atas - ALIGN_EPS < y_a < y0 + ALIGN_EPS
                                and y_b > batas_atas + ALIGN_EPS):
                            obj_seg[kol_vo].append(min(y_b, y0))
                            obj_atas[kol_vo].append(max(y_a, batas_atas))

            seg_kiri = [y for y in isi[0] if batas_atas < y < y0
                        and not any(ya - ALIGN_EPS <= y <= yb + ALIGN_EPS for ya, yb in rentang_lain[0])]
            seg_kanan = [y for y in isi[1] if batas_atas < y < y0
                         and not any(ya - ALIGN_EPS <= y <= yb + ALIGN_EPS for ya, yb in rentang_lain[1])]
            ada_kiri = bool(seg_kiri or obj_seg[0])
            ada_kanan = bool(seg_kanan or obj_seg[1])
            y_awal_seg = max(batas_atas, 102.4)
            obj_lebih_bawah = (
                (obj_seg[0] and (not seg_kiri or max(obj_seg[0]) > max(seg_kiri) + 2.2 + ALIGN_EPS))
                or (obj_seg[1] and (not seg_kanan or max(obj_seg[1]) > max(seg_kanan) + 2.2 + ALIGN_EPS))
            )
            if seg_kiri and seg_kanan and not obj_lebih_bawah:
                max_kiri = max(seg_kiri)
                max_kanan = max(seg_kanan)
                selisih = abs(max_kiri - max_kanan)
                if max_kanan > max_kiri + ALIGN_EPS:
                    # Aturan 7: Kolom kanan dilarang lebih panjang dari kolom kiri pada segmen mana pun.
                    timpang[0] = max(timpang.get(0, 0.0), max(TOLERANSI_OBJEK_PENUH_PT + 11.0, max_kanan - max_kiri))
                elif selisih > TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS:
                    timpang[1] = max(timpang.get(1, 0.0), selisih)
            elif ada_kiri or ada_kanan:
                bawah_kiri = max([y + 2.2 for y in seg_kiri] + obj_seg[0], default=y_awal_seg)
                bawah_kanan = max([y + 2.2 for y in seg_kanan] + obj_seg[1], default=y_awal_seg)
                atas_kiri = min([y - 8.8 for y in seg_kiri] + obj_atas[0], default=y_awal_seg)
                atas_kanan = min([y - 8.8 for y in seg_kanan] + obj_atas[1], default=y_awal_seg)
                if ada_kiri and not ada_kanan and bawah_kiri - max(y_awal_seg, atas_kiri) > TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS:
                    timpang[1] = max(timpang.get(1, 0.0), bawah_kiri - y_awal_seg)
                elif ada_kanan and not ada_kiri and bawah_kanan - max(y_awal_seg, atas_kanan) > TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS:
                    timpang[0] = max(timpang.get(0, 0.0), max(TOLERANSI_OBJEK_PENUH_PT + 11.0, bawah_kanan - y_awal_seg))
                elif ada_kiri and ada_kanan:
                    if bawah_kanan > bawah_kiri + 11.0 + ALIGN_EPS:
                        timpang[0] = max(timpang.get(0, 0.0), max(TOLERANSI_OBJEK_PENUH_PT + 11.0, bawah_kanan - bawah_kiri))
                    elif bawah_kiri - bawah_kanan > TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS:
                        timpang[1] = max(timpang.get(1, 0.0), bawah_kiri - bawah_kanan)

            akhir_obj = pos.get("objek-akhir-{}".format(nomor))
            if akhir_obj and akhir_obj[1] == h:
                y1 = akhir_obj[3]
            elif akhir_obj and akhir_obj[1] > h:
                y1 = target
            else:
                tinggi = float(v[0][1]) if isinstance(v[0], (tuple, list)) else 0.0
                y1 = v[3] + tinggi
            batas_atas = max(batas_atas, y1)

        for kol in (0, 1):
            target_ef = target
            if (h, kol) in bawah_satu_kol and (h, kol) not in tutup:
                y0_b, clr_b = bawah_satu_kol[(h, kol)]
                isi_atas = [y for y in isi[kol] if y < y0_b - ALIGN_EPS]
                if isi_atas:
                    akhir = max(isi_atas)
                    target_ef = fase + math.floor((y0_b - clr_b - 2.2 - fase + 0.001) / 11.0) * 11.0
                else:
                    tutup.add((h, kol))
                    akhir = max(isi[kol]) if isi[kol] else None
            else:
                akhir = max(isi[kol]) if isi[kol] else None
            kurang = target_ef - (akhir if akhir is not None else fase - 11.0)
            penuh_objek = (h, kol) in tutup and kurang >= -ALIGN_EPS
            if penuh_objek:
                kurang = 0.0
            segmen_objek = False
            if kol in timpang:
                kurang = timpang[kol]
                penuh_objek = False
                segmen_objek = True
            hasil.append(dict(hal=h, kol=kol, kurang=kurang,
                              target=target, akhir_baseline=akhir,
                              baris_terlihat=len(set(round(y, 3) for y in isi[kol])),
                              kosong=akhir is None and not penuh_objek,
                              penuh_objek=penuh_objek,
                              segmen_objek=segmen_objek,
                              batas=[], seg=0, akhir=h == jumlah))
    # FIX-AKHIR-SEIMBANG: bila sisa naskah tidak cukup untuk mengisi satu kolom penuh,
    # halaman terakhir boleh berakhir seimbang (kiri/kanan sama tinggi, selisih <= 2 baris).
    # Kedua kolom sama-sama pendek karena dokumen memang habis, bukan kolom kiri menggantung.
    if jumlah >= 2:
        kiri = [x for x in hasil if x["hal"] == jumlah and x["kol"] == 0]
        kanan = [x for x in hasil if x["hal"] == jumlah and x["kol"] == 1]
        # Aturan 7: Pada halaman terakhir seimbang, kolom kanan tidak boleh lebih panjang
        # dari kolom kiri (kanan["kurang"] >= kiri["kurang"] - ALIGN_EPS) dan selisih <= 2 baris.
        if (len(kiri) == 1 and len(kanan) == 1 and not kiri[0]["kosong"]
                and not kanan[0]["kosong"] and kiri[0]["baris_terlihat"] > 0
                and kanan[0]["baris_terlihat"] > 0
                and kanan[0]["kurang"] >= kiri[0]["kurang"] - ALIGN_EPS
                and (kanan[0]["kurang"] - kiri[0]["kurang"])
                <= TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS):
            kiri[0]["akhir_seimbang"] = True
            kanan[0]["akhir_seimbang"] = True
    footer, _ = footer_target(pdf, teks)
    hasil = tandai_pengecualian_target(hasil, footer)
    return tandai_batas_akhir(hasil, pos, teks, jumlah)

KOREKSI_SEIMBANG_PT = -5.5     # = KOREKSI-SEIMBANG di template_jurnal.typ (FIX-SEIMBANG-GENAP)


def _jumlah_halaman(pdf):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    with fitz.open(pdf) as doc:
        return doc.page_count


def _belah_paragraf(t, n, teks_baris):
    """Inti FIX-SEJAJARKAN: pecah paragraf/entri #pm(n) tepat sebelum baris `teks_baris`.
    Hasil (awal, akhir, bag1, bag2, ref) atau None. Markup #emph/#strong yang terbuka ditutup
    di bag1 dan dibuka lagi di bag2; titik potong di dalam tautan, rumus, atau kurung -> None."""
    m = re.search(r"^((?:#referensi\[|#wawancara\[)?)#pm\({}\);".format(n), t, flags=re.M)
    if not m:
        return None
    prefix = m.group(1)
    ref = "wawancara" if "#wawancara[" in prefix else ("referensi" if "#referensi[" in prefix else "")
    awal = m.end()
    akhir = t.find("\n", awal)
    akhir = len(t) if akhir < 0 else akhir
    if ref:
        if not t[awal:akhir].endswith("]"):
            return None
        akhir -= 1
    isi = t[awal:akhir]
    # peta karakter alfanumerik terlihat -> indeks sumber
    peta, alnum, k = [], [], 0
    while k < len(isi):
        if isi[k] == "\\":
            k += 2
            continue
        mt = re.match(r"#(?:emph|strong)\[", isi[k:])
        if mt:
            k += mt.end()
            continue
        if isi[k].isalnum():
            alnum.append(isi[k].lower())
            peta.append(k)
        k += 1
    alnum = "".join(alnum)
    sasaran = "".join(c.lower() for c in teks_baris if c.isalnum())[:24]
    if len(sasaran) < 6:
        return None
    i = alnum.find(sasaran, 1)
    if i <= 0:
        return None
    pos = peta[i]
    while True:                                   # markup pembuka tepat sebelum kata ikut ke bagian 2
        mt = re.search(r"#(?:emph|strong)\[$", isi[:pos])
        if not mt:
            break
        pos = mt.start()
    # periksa konteks pada titik potong
    tumpukan, rumus, k = [], False, 0
    while k < pos:
        if isi[k] == "\\":
            k += 2
            continue
        mt = re.match(r"#(?:emph|strong)\[", isi[k:])
        if mt:
            tumpukan.append(mt.group(0))
            k += mt.end()
            continue
        panggil = re.match(r"#[A-Za-z_][\w-]*\(", isi[k:])
        if panggil:
            tutup = akhir_kurung_typst(isi, k + panggil.end() - 1)
            if tutup is None or tutup >= pos:
                return None
            k = tutup + 1
            continue
        c = isi[k]
        if c == "$":
            rumus = not rumus
        elif c == "[":
            tumpukan.append("[")
        elif c == "]":
            if not tumpukan:
                return None
            tumpukan.pop()
        k += 1
    if rumus or any(x == "[" for x in tumpukan):
        return None
    bag1 = isi[:pos].rstrip() + "]" * len(tumpukan) + "#linebreak()"
    bag2 = "".join(tumpukan) + isi[pos:]
    return awal, akhir, bag1, bag2, ref


def cari_pita_atas_tabel(pdf, pos, hal, k, teks, y_mulai=100.0):
    """FIX-PITA-TABEL-ATAS: cari titik potong untuk memindahkan pita teks 1-4 baris
    di puncak halaman `hal` (di atas tabel `k`) ke segmen kolom sesudah tabel `k`."""
    I = pos.get("objek-info-{}".format(k))
    if not I or I[1] != hal:
        return None
    y_batas = I[3]
    per_y = {}
    for bl in blok_teks(pdf, hal):
        for l in bl.get("lines", []):
            if not (l["spans"] and abs(l["spans"][0]["size"] - 11) < 0.2
                    and y_mulai < l["bbox"][1] < y_batas and l["bbox"][2] < 300):
                continue
            t_l = "".join(sp["text"] for sp in l["spans"]).strip()
            if not t_l:
                continue
            y = round(l["spans"][0]["origin"][1], 1)
            per_y.setdefault(y, []).append((l["bbox"][0], t_l))
    if not per_y:
        return None
    y0, potongan0 = sorted(per_y.items())[0]
    teks_l = " ".join(t for _, t in sorted(potongan0))
    for kk, v in pos.items():
        if kk.startswith("pm-") and v[1] == hal and v[2] < 300 and abs((v[3] + 8.8) - y0) <= 1.5:
            return ("putus_par", int(kk[3:]), "")
    semua_pm = sorted((v[1], 0 if v[2] < 300 else 1, v[3], int(kk[3:]))
                      for kk, v in pos.items() if kk.startswith("pm-"))
    sebelum = [u for u in semua_pm if u[0] < hal or (u[0] == hal and u[1] == 0 and u[2] < y0)]
    if not sebelum:
        return None
    return ("belah_par", sebelum[-1][3], teks_l)


def pindah_pita_atas_tabel(teks, k, mode, n_pm, teks_baris=""):
    """FIX-PITA-TABEL-ATAS: pindahkan pita teks di atas tabel `k` ke awal `#kolom[` sesudah tabel.
    mode 'putus_par' = pita mulai di awal paragraf (seluruh paragraf dipindah);
    mode 'belah_par' = pita adalah lanjutan paragraf yang dimulai di halaman sebelumnya."""
    p, q = blok_objek(teks, k)
    if p < 2 or teks[p - 2:p] != "]\n" or not teks.startswith("#kolom[\n", q):
        return teks
    if mode == "putus_par":
        m = re.search(r"^#pm\({}\);".format(n_pm), teks[:p - 2], flags=re.M)
        if not m:
            return teks
        potongan = teks[m.start():p - 2].strip()
        sebelum = teks[:m.start()].rstrip() + "\n"
        sisip = q + len("#kolom[\n")
        return sebelum + teks[p - 2:sisip] + potongan + "\n\n" + teks[sisip:]
    r = _belah_paragraf(teks, n_pm, teks_baris)
    if not r or r[4]:
        return teks
    awal, akhir, bag1, bag2, _ = r
    sementara = teks[:awal] + bag1 + teks[akhir:]
    p2, q2 = blok_objek(sementara, k)
    if p2 < 2 or sementara[p2 - 2:p2] != "]\n" or not sementara.startswith("#kolom[\n", q2):
        return teks
    sisip = q2 + len("#kolom[\n")
    return sementara[:sisip] + "#lanjutan[" + bag2 + "]\n\n" + sementara[sisip:]


def potong_paragraf(t, n, teks_baris):
    """Pisah sambil mempertahankan identitas paragraf untuk QA dua baris per fragmen."""
    r = _belah_paragraf(t, n, teks_baris)
    if not r:
        return None
    awal, akhir, bag1, bag2, ref = r
    if "#pf({},".format(n) in t:
        return None
    bag1 = bag1.removesuffix("#linebreak()") + "#pe({})#linebreak()".format(n)
    bag2 = "#pf({}, 1);".format(n) + bag2.replace("#pe({})".format(n),
                                                               "#pe({}, bagian: 1)".format(n))
    if ref == "wawancara":
        return t[:awal] + bag1 + "]\n#colbreak()\n#wawancara[" + bag2 + "]" + t[akhir + 1:]
    if ref:
        return t[:awal] + bag1 + "]\n#colbreak()\n#lanjutan-ref[" + bag2 + "]" + t[akhir + 1:]
    return t[:awal] + bag1 + "\n\n#colbreak()\n#lanjutan[" + bag2 + "]" + t[akhir:]



def terapkan_potong(t, spec):
    """FIX-SEJAJARKAN: ("putus", n) = #colbreak() sebelum paragraf/judul/entri n;
    ("belah", n, teks) = potong paragraf n sebelum baris `teks`."""
    if spec[0] == "belah":
        return potong_paragraf(t, spec[1], spec[2])
    m = re.search(r"^(?:=+ |#referensi\[|#wawancara\[)?#pm\({}\);".format(spec[1]), t, flags=re.M)
    if not m:
        return None
    return t[:m.start()] + "#colbreak()\n\n" + t[m.start():]


def cari_potong(pdf, pos, hal, kol, m, teks):
    """FIX-SEJAJARKAN: titik potong agar m baris terakhir kolom `kol` halaman `hal` pindah ke kolom
    berikut, tanpa menyisakan satu baris di bawah maupun memindah satu baris sendirian. None bila
    tidak mungkin (mis. baris itu bagian tautan/rumus)."""
    if m <= 0:
        return None
    rentang_obj = []
    for lbl, vo in pos.items():
        mo = re.fullmatch(r"objek-info-(\d+)", lbl)
        if not mo or not objek_penuh_teks(teks, int(mo[1])):
            continue
        no = int(mo[1])
        ak = pos.get("objek-akhir-{}".format(no))
        if ak:
            h_start, y_start = vo[1], vo[3]
            h_end, y_end = ak[1], ak[3]
        else:
            tg = float(vo[0][1]) if isinstance(vo[0], (tuple, list)) else 0.0
            h_start, y_start = vo[1], vo[3]
            h_end, y_end = vo[1], vo[3] + tg
        if h_start <= hal <= h_end:
            y_a = y_start if h_start == hal else 0.0
            y_b = y_end if h_end == hal else 1000.0
            rentang_obj.append((y_a, y_b))

    per_y = {}                       # potongan teks pada garis dasar yang sama = satu baris
    for l in baris_narasi_terlihat(pdf, hal):
        if not (l["spans"] and abs(l["spans"][0]["size"] - 11) < 0.2 and l["spans"][0]["origin"][1] > 102.4
                and l["kol"] == kol):
            continue
        if any(ya - 0.5 <= l["spans"][0]["origin"][1] <= yb + 0.5 for ya, yb in rentang_obj):
            continue
        if all("math" in sp["font"].lower() for sp in l["spans"] if sp["text"].strip()) or \
                RE_NOMOR_RUMUS.match("".join(sp["text"] for sp in l["spans"]).strip()):
            continue
        y = round(l["spans"][0]["origin"][1], 1)
        per_y.setdefault(y, []).append((l["bbox"][0], "".join(sp["text"] for sp in l["spans"])))
    garis = sorted((y, " ".join(t for _, t in sorted(v))) for y, v in per_y.items())
    if len(garis) < m + 2:
        return None
    ref_ids = {int(n) for n in re.findall(r"#referensi\[#pm\((\d+)\)", teks)}

    def awal_baris(h, c):
        return sorted((round(v[3] if int(k[3:]) in ref_ids else v[3] + 8.8, 1), int(k[3:]))
                      for k, v in pos.items() if k.startswith("pm-") and v[1] == h
                      and (0 if v[2] < 300 else 1) == c)
    mulai = awal_baris(hal, kol)
    awal_y = {round(y): n for y, n in mulai}
    y_l, teks_l = garis[-m]
    ys = [y for y, _ in garis]
    idx = len(garis) - m

    def awal_paragraf_sebelum(y):
        cocok = [(yy, n) for yy, n in mulai if yy <= y + 0.5]
        return cocok[-1] if cocok else (None, None)
    # sisa paragraf di atas titik potong harus >= 2 baris di kolom ini
    y_atas = ys[idx - 1]
    ya, _ = awal_paragraf_sebelum(y_atas)
    baris_atas = sum(1 for y in ys[:idx] if ya is None or y >= ya - 0.5)
    if round(y_l) in awal_y:                         # tepat di awal paragraf -> putus di batas
        if baris_atas < 2:
            return None
        return ("putus", awal_y[round(y_l)])
    yp, n_p = awal_paragraf_sebelum(y_l)
    if n_p is None:                                  # paragraf mulai di kolom/halaman sebelumnya
        semua = sorted(((v[1], 0 if v[2] < 300 else 1, v[3], int(k[3:])) for k, v in pos.items()
                        if k.startswith("pm-")))
        sebelum = [u for u in semua if u[:2] < (hal, kol) or (u[:2] == (hal, kol) and u[2] < y_l)]
        if not sebelum:
            return None
        n_p = sebelum[-1][3]
    baris_n_atas = sum(1 for y in ys[:idx] if yp is None or y >= yp - 0.5)
    if baris_n_atas < 2:
        return None
    # baris paragraf n yang ikut pindah (sampai awal paragraf berikutnya) + lanjutannya >= 2
    pindah_n = 0
    for y in ys[idx:]:
        if round(y) in awal_y and y > y_l + 0.5:
            break
        pindah_n += 1
    if pindah_n == 1 and pindah_n == len(ys[idx:]):
        h2, c2 = (hal, 1) if kol == 0 else (hal + 1, 0)
        lanjut = awal_baris(h2, c2)
        garis2 = sorted(round(l["spans"][0]["origin"][1], 1) for l in (baris_narasi_terlihat(pdf, h2) if h2 <= _jumlah_halaman(pdf) else []) if l["spans"] and abs(l["spans"][0]["size"] - 11) < 0.2
                        and l["spans"][0]["origin"][1] > 102.4 and l["kol"] == c2)
        if not garis2 or any(abs(y - garis2[0]) < 0.6 for y, _ in lanjut):
            return None                              # satu baris pindah sendirian
    elif pindah_n == 1:
        return None
    if potong_paragraf(teks, n_p, teks_l) is None:
        return None
    return ("belah", n_p, teks_l)


def ratakan_kolom(teks, body_path, workdir, laporan):
    """Pencarian titik putus sah; tidak mengubah spasi, penalti widow, atau footer."""
    import time
    main = os.path.join(workdir, "main.typ")
    pdf = os.path.join(workdir, "_ukur.pdf")
    antre = [teks]
    dilihat = set()
    terbaik = None
    mulai = time.monotonic()
    for _ in range(3):
        anak = []
        for sumber in antre:
            if sumber in dilihat:
                continue
            dilihat.add(sumber)
            with open(body_path, "w", encoding="utf-8") as f:
                f.write(sumber)
            rc, _, err = jalankan([TYPST, "compile", "--root", BASE, "--font-path",
                                    os.path.join(BASE, "fonts"), main, pdf])
            pos, _ = kueri_posisi(main) if rc == 0 else (None, "")
            if pos is None or "did not converge" in err:
                continue
            ukuran = ukur_layout_ketat(pdf, pos, sumber)
            nilai = kunci_layout_ketat(ukuran)
            if terbaik is None or nilai < terbaik[0]:
                terbaik = (nilai, sumber, ukuran)
            if ukuran["lulus"]:
                break
            anak.extend(calon_potong_ketat(sumber, pdf, pos, ukuran))
            if time.monotonic() - mulai > min(30, BATAS_WAKTU[0]):
                break
        if terbaik and terbaik[2]["lulus"]:
            break
        antre = list(dict.fromkeys(anak))[:6]
        if not antre:
            break
    if terbaik is None:
        raise RuntimeError("Perataan gagal: kompilasi atau metadata tidak sah")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(terbaik[1])
    return terbaik[1], terbaik[2]["kolom"]




# Semua batas di bawah adalah batas pengukuran, bukan izin menyisakan baris.
ALIGN_EPS = 0.05
KANAN_MAKS_PT = BATAS_KANAN_NONAKHIR_PT
WAKTU_KANDIDAT_DETIK = 240
BATAS_WAKTU = [WAKTU_KANDIDAT_DETIK]
N_KANDIDAT = 4


def fragmen_paragraf_ketat(pdf, pos, teks):
    """Hitung fragmen berdasarkan identitas paragraf asli, termasuk hasil colbreak manual."""
    area = area_objek_ketat(pos, teks)
    lebar = {k: v for k, v in pos.items() if k.startswith("objek-info-")
             and objek_penuh_teks(teks, int(k.rsplit("-", 1)[1]))}

    def urut(h, kol, y):
        pita = sum(v[1] == h and v[3] < y for v in lebar.values())
        return (h, pita, kol, y)

    baris = {}
    for h in range(1, _jumlah_halaman(pdf) + 1):
        for line in baris_narasi_terlihat(pdf, h):
            sp = [s for s in line["spans"] if s["text"].strip()]
            if not sp or abs(sp[0]["size"] - 11) > 0.2:
                continue
            x0, y0, x1, _ = line["bbox"]
            kol = line["kol"]
            y = sp[0]["origin"][1]
            if kol < 0 or y <= 102.4:
                continue
            if all("math" in s["font"].lower() for s in sp):
                continue
            if any(a - 0.1 <= y <= b + 0.1 for a, b in area.get((h, kol), [])):
                continue
            key = urut(h, kol, round(y, 2))
            baris[key] = True
    starts, ends = {}, {}
    for label, v in pos.items():
        m = re.fullmatch(r"frag-(awal|akhir)-(\d+)-(\d+)", label)
        if m:
            target = starts if m[1] == "awal" else ends
            target[(int(m[2]), int(m[3]))] = v
    expected = set(int(n) for n in re.findall(r"#pm\((\d+)\)", teks))
    headings = set(int(n) for n in re.findall(r"^=+ #pm\((\d+)\)", teks, re.M))
    pm_beku = pm_beku_teks(teks)
    masalah, tunggal = [], []
    for n in sorted(expected):
        if n in pm_beku:
            continue
        bagian = sorted(key for key in starts if key[0] == n)
        if not bagian or any(key not in ends for key in bagian):
            masalah.append("Penanda akhir paragraf {} tidak lengkap".format(n))
            continue
        if n in headings:
            continue
        counts = {}
        for key in bagian:
            a, b = starts[key], ends[key]
            lo = urut(a[1], 0 if a[2] < 300 else 1, a[3] - 0.1)
            hi = urut(b[1], 0 if b[2] < 300 else 1, b[3] + 8.9)
            berikut = [urut(v[1], 0 if v[2] < 300 else 1, v[3] - 0.1)
                       for other, v in starts.items() if other != key
                       and urut(v[1], 0 if v[2] < 300 else 1, v[3] - 0.1) > lo]
            batas_berikut = min(berikut) if berikut else None
            for line in baris:
                if lo <= line <= hi and (batas_berikut is None or line < batas_berikut):
                    lokasi = line[:3]
                    counts.setdefault(lokasi, set()).add(line[-1])
        if not counts:
            sumber = next((line for line in teks.splitlines()
                           if "#pm({})".format(n) in line), "")
            if "$" not in sumber and "#rumus" not in sumber:
                masalah.append("Baris paragraf {} tidak dapat diukur".format(n))
        if len(counts) > 1:
            for (h, _, kol), ys in counts.items():
                if len(ys) == 1:
                    tunggal.append(dict(jenis="fragmen", hal=h, kol=kol,
                                        y=min(ys), pm=n))
    if set(starts) != set(ends):
        masalah.append("Pasangan penanda fragmen tidak lengkap")
    return tunggal, masalah


def ukur_layout_ketat(pdf, pos, teks):
    """Satu gerbang untuk pencarian dan PDF final; gagal tertutup jika metadata hilang."""
    jumlah = _jumlah_halaman(pdf)
    kolom = ukur_kolom(pdf, pos, teks)
    masalah = []
    for x in kolom:
        gap = x["kurang"]
        if not math.isfinite(gap):
            masalah.append("Koordinat kolom tidak sah")
        elif not kolom_sah(x, jumlah):
            masalah.append(pesan_kolom(x, jumlah))
    if len(kolom) != 2 * jumlah:
        masalah.append("Pengukuran kedua kolom tidak lengkap")
    float_bawah_kiri = set()
    for lbl_o, vo in pos.items():
        mo = re.fullmatch(r"objek-info-(\d+)", lbl_o)
        if not mo:
            continue
        no = int(mo[1])
        if objek_penuh_teks(teks, no) or not re.search(r"#(?:gambar|tabel)\([^\n]*\bnomor: {}\b".format(no), teks):
            continue
        if vo[2] < 290:
            tg_o = float(vo[0][1]) if isinstance(vo[0], (tuple, list)) and len(vo[0]) > 1 else 0.0
            if tg_o == 0:
                ak_o = pos.get("objek-akhir-{}".format(no))
                y1_o = (ak_o[3] if ak_o[1] == vo[1] else 762.4) if ak_o else vo[3]
            else:
                y1_o = vo[3] + tg_o
            if y1_o >= 720.0:
                float_bawah_kiri.add(vo[1])
    for h_cek in range(1, jumlah + 1):
        xs_h = [x for x in kolom if x["hal"] == h_cek]
        if len(xs_h) == 2 and h_cek not in float_bawah_kiri:
            kr_h = next((x for x in xs_h if x["kol"] == 0), None)
            kn_h = next((x for x in xs_h if x["kol"] == 1), None)
            if (kr_h and kn_h and not kr_h.get("penuh_objek") and not kn_h.get("penuh_objek")
                    and kr_h.get("akhir_baseline") is not None
                    and kn_h.get("akhir_baseline") is not None
                    and kn_h["akhir_baseline"] > kr_h["akhir_baseline"] + ALIGN_EPS):
                masalah.append("Kolom kanan lebih panjang dari kolom kiri hlm fisik {}".format(h_cek))
    grid = baris_luar_grid(pdf, pos, teks)
    tunggal = baris_tunggal(pdf, pos, teks)
    ekstra, galat = fragmen_paragraf_ketat(pdf, pos, teks)
    for t in ekstra:
        sama = next((z for z in tunggal if z["hal"] == t["hal"]
                     and z["kol"] == t["kol"] and abs(z["y"] - t["y"]) < 1), None)
        if sama is None:
            tunggal.append(t)
        else:
            sama.setdefault("pm", t["pm"])
    masalah += galat
    pm_beku = pm_beku_teks(teks)
    for t in tunggal:
        if t.get("pm") and t.get("pm") in pm_beku:
            continue
        masalah.append("Baris tunggal ({}) hlm fisik {}, kolom {}, paragraf {}".format(
            t["jenis"], t["hal"], "kiri" if t["kol"] == 0 else "kanan", t.get("pm", "?")))
    for g in grid:
        masalah.append("{} baris di luar grid hlm fisik {}, kolom {}".format(
            g["baris"], g["hal"], g["kol"]))
    for h, n in baris_luber(pdf):
        masalah.append("{} baris melewati dasar hlm fisik {}".format(n, h))
    for k, h, lebih in objek_luber(pos, teks):
        masalah.append("Objek {} meluber {:.2f} pt di hlm fisik {}".format(k, lebih, h))
    if re.search(r"#set\s+text\([^)\n]*(?:tracking|spacing|widow:\s*0%|orphan:\s*0%)", teks):
        masalah.append("Aturan tipografi terlarang dalam body")
    _, footer_galat = footer_target(pdf, teks)
    masalah += footer_galat
    for x in kolom:
        if x.get("footer_naik") and not x.get("pengecualian_target"):
            masalah.append("Footer hlm fisik {} dinaikkan tanpa kesejajaran kedua kolom".format(x["hal"]))
    for h in sorted({x["hal"] for x in kolom if x.get("footer_naik")}):
        xs = [x for x in kolom if x["hal"] == h]
        if len(xs) == 2 and all(x["akhir_baseline"] is not None for x in xs):
            beda = abs(xs[0]["akhir_baseline"] - xs[1]["akhir_baseline"])
            if beda > ALIGN_EPS:
                masalah.append("Baseline teks tidak sejajar hlm fisik {}: selisih {:.2f} pt".format(h, beda))
    pengecualian = [dict(hal=x["hal"], footer_naik=x["footer_naik"],
                        sisa_fisik=x["kurang"]) for x in kolom
                    if x["kol"] == 0 and x.get("pengecualian_target")]
    return dict(halaman=jumlah, kolom=kolom, tunggal=tunggal, grid=grid,
                pengecualian=pengecualian,
                masalah=list(dict.fromkeys(masalah)), lulus=not masalah)


def kunci_layout_ketat(u):
    """Kunci ranking: kolom kiri pendek adalah pelanggaran wajib, bukan sekadar
    kekurangan estetis; jangan pernah menukarnya dengan beberapa kolom kanan pendek."""
    kiri = sum(1 for x in u["kolom"]
               if x["kol"] == 0 and not kolom_sah(x, u["halaman"]))
    sisa = sum(abs(x["kurang"]) for x in u["kolom"]
               if not kolom_sah(x, u["halaman"]))
    return (len(u["masalah"]) + 999 * kiri, round(sisa, 4), u["halaman"])


def calon_potong_ketat(teks, pdf, pos, u):
    """Coba titik putus di halaman bermasalah dan sebelumnya; min. dua baris per fragmen."""
    buruk = sorted({x["hal"] for x in u["kolom"]
                    if not kolom_sah(x, u["halaman"])}
                   | {t["hal"] for t in u["tunggal"]})
    halaman = sorted({h for p in buruk[:2] for h in (p - 1, p) if h >= 1})
    hasil = []
    for h in halaman:
        for kol in (0, 1):
            for m in (1, 2, 3):
                spec = cari_potong(pdf, pos, h, kol, m, teks)
                if not spec:
                    continue
                # Satu belahan manual per paragraf; pecahan alami tetap bisa lintas halaman.
                if spec[0] == "belah" and "#pf({},".format(spec[1]) in teks:
                    continue
                t = terapkan_potong(teks, spec)
                if t and t != teks:
                    hasil.append(t)
    return hasil


def calon_objek_ketat(body, keputusan, pos, u):
    import copy
    buruk = {x["hal"] for x in u["kolom"] if not kolom_sah(x, u["halaman"]) or (
        x["kol"] == 1 and x["hal"] != u["halaman"] and x["kurang"] > ALIGN_EPS)}
    buruk |= {t["hal"] for t in u["tunggal"]} | {g["hal"] for g in u["grid"]}
    for m_str in u.get("masalah", []):
        for h_m in re.findall(r"hlm(?: fisik)? (\d+)|Halaman (\d+)", m_str):
            h_val = int(h_m[0] or h_m[1])
            buruk.add(h_val - HALAMAN_AWAL_CETAK[0] + 1 if h_val >= HALAMAN_AWAL_CETAK[0] and HALAMAN_AWAL_CETAK[0] > 1 else h_val)
    tambahan_k = set()
    gambar_hal = sorted(
        (pos["objek-info-" + str(k)][1], k)
        for k in keputusan
        if ("objek-info-" + str(k)) in pos
        and body[slice(*blok_objek(body, k))].startswith("#gambar(")
        and "chart_render_" not in body[slice(*blok_objek(body, k))]
    )
    for h_b in sorted(buruk):
        sbl = [kg for hg, kg in gambar_hal if hg < h_b]
        if sbl:
            tambahan_k.add(sbl[-1])
    kandidat_objek = []
    for k, kp in sorted(keputusan.items()):
        blok = body[slice(*blok_objek(body, k))]
        if not blok.startswith(("#gambar(", "#tabel(")):
            continue
        info = pos.get("objek-info-" + str(k))
        if not info or (not any(abs(info[1] - h) <= 1 for h in buruk) and k not in tambahan_k):
            continue
        kandidat_objek.append((k, kp, blok, info))
    min_buruk = min(buruk) if buruk else 0
    kandidat_objek.sort(key=lambda item: (0 if item[3][1] == min_buruk else (1 if abs(item[3][1] - min_buruk) <= 1 else (2 if item[3][1] in buruk else 3)), item[0]))
    for k, kp, blok, info in kandidat_objek:
        uk = ukuran_gambar(blok)
        akhir = pos.get("objek-akhir-" + str(k))
        is_tabel = blok.startswith("#tabel(")
        if is_tabel:
            p, q = blok_objek(body, k)
            tail = body[q:]
            batas = re.search(r"^= #pm", tail, re.M)
            if batas:
                tail = tail[:batas.start()]
            pm_list = [int(x) for x in re.findall(r"#pm\((\d+)\);", tail)]
            tgt_now = kp.get("target")
            ordered_pms = (
                sorted(pm_list[1:5], key=lambda n: (abs(n - tgt_now), 0 if n > tgt_now else 1))
                if kp.get("jenis") == "geser" and tgt_now
                else pm_list[1:5]
            )
            geser_cands = []
            for nomor in ordered_pms:
                if kp.get("jenis") == "geser" and tgt_now == nomor:
                    continue
                kp2 = copy.deepcopy(keputusan)
                kp2[k] = dict(kp, jenis="geser", target=nomor)
                for mk in ("mode", "n_pm", "teks_baris", "maju", "maju_hal", "lanjut"):
                    kp2[k].pop(mk, None)
                geser_cands.append(kp2)
            float_cands = []
            # FIX-TABEL-BUKAN-FLOAT: tabel selebar halaman tidak boleh menjadi float utuh
            # jika panjang/lintas halaman. Namun tabel satu halaman boleh menjadi float "bawah" atau "atas"
            # (mis. Table 6 float bawah untuk menghilangkan pita teks).
            if not (akhir and info and akhir[1] != info[1]):
                for jns in ("bawah", "atas"):
                    if kp.get("jenis") != jns:
                        kp2 = copy.deepcopy(keputusan)
                        kp2[k] = dict(kp, jenis=jns)
                        for mk in ("target", "mode", "n_pm", "teks_baris", "maju", "maju_hal", "lanjut"):
                            kp2[k].pop(mk, None)
                        float_cands.append(kp2)
            for kp2 in (geser_cands + float_cands if kp.get("jenis") == "geser" else float_cands + geser_cands):
                yield kp2
            if kp.get("jenis") != "tempat":
                kp2 = copy.deepcopy(keputusan)
                kp2[k] = dict(kp, jenis="tempat")
                for mk in ("target", "mode", "n_pm", "teks_baris", "maju", "maju_hal", "lanjut"):
                    kp2[k].pop(mk, None)
                yield kp2
            continue
        skala = kp.get("skala", 1.0)
        if uk:
            slot = 442.2 if objek_penuh(blok) else LEBAR_KOLOM_PT
            tinggi = float(info[0][1])
            extra = 8.0 if kp.get("jenis") in ("tunda", "atas", "bawah") else 22.0
            tinggi_nyata = tinggi + (
                4.0 if (kp.get("jenis") == "bawah" or (kp.get("jenis") == "tunda" and not objek_penuh(blok)))
                else 0.0
            )
            h_gambar = uk[1] * skala
            tetap = tinggi_nyata - h_gambar
            slot_asal = math.ceil((tinggi_nyata + extra) / 11.0 - 0.001)
            # Prioritaskan ambang slot grid eksak dulu, lalu persentase; deduplikasi per slot 11 pt.
            kandidat_skala = []
            for delta in (-1, 1, -2, 2, -3, 3, -4):
                target = (slot_asal + delta) * 11.0
                kandidat_skala.append(round((target - tetap - extra - 0.2) / uk[1], 3))
            kandidat_skala.extend(sorted(
                (round(skala * (1 + arah * persen / 100.0), 3)
                 for persen in range(1, 4) for arah in (-1, 1)),
                key=lambda s: abs(s - skala),
            ))
            slot_terlihat = {slot_asal}
            for s in kandidat_skala:
                if (not 0.70 <= s <= 1.20 or abs(s - skala) < 0.001
                        or uk[0] * s > slot + ALIGN_EPS
                        or (s > skala and uk[3] * 72 / (uk[0] * s) < 150)):
                    continue
                slot_baru = math.ceil((tetap + uk[1] * s + extra) / 11.0 - 0.001)
                if slot_baru in slot_terlihat:
                    continue
                slot_terlihat.add(slot_baru)
                kp2 = copy.deepcopy(keputusan)
                kp2[k] = dict(kp, skala=s)
                yield kp2
        if kp.get("jenis") == "tunda" and kp.get("maju") and kp.get("lanjut"):
            kp2 = copy.deepcopy(keputusan)
            kp2[k] = {k_: v_ for k_, v_ in kp.items() if k_ not in ("maju", "maju_hal", "lanjut")}
            kp2[k]["target"] = kp["lanjut"]
            yield kp2
        for jenis in ("tempat", "atas", "bawah"):
            if kp.get("jenis") == jenis:
                continue
            kp2 = copy.deepcopy(keputusan)
            kp2[k] = dict(kp, jenis=jenis)
            for mk in ("target", "maju", "maju_hal", "lanjut"):
                kp2[k].pop(mk, None)
            yield kp2
        target = next((int(label[3:]) for label, v in sorted(pos.items(),
                      key=lambda item: (item[1][1], item[1][3]))
                       if label.startswith("pm-") and v[1] > info[1]), None)
        if target is not None:
            kp2 = copy.deepcopy(keputusan)
            kp2[k] = dict(kp, jenis="tunda", target=target)
            for mk in ("maju", "maju_hal", "lanjut"):
                kp2[k].pop(mk, None)
            yield kp2




def _lewati_literal_typst(teks, i, isi=False):
    """Lewati escape, komentar, string kode, raw, atau math; None bila tidak ditutup."""
    if teks[i] == "\\":
        return min(i + 2, len(teks))
    if teks.startswith("//", i) and (not isi or i == 0
            or teks[i - 1].isspace() or teks[i - 1] in "([{"):
        j = teks.find("\n", i + 2)
        return len(teks) if j < 0 else j + 1
    if teks.startswith("/*", i):
        depth, j = 1, i + 2
        while j < len(teks) and depth:
            if teks.startswith("/*", j):
                depth += 1
                j += 2
            elif teks.startswith("*/", j):
                depth -= 1
                j += 2
            else:
                j += 1
        return j if depth == 0 else None
    c = teks[i]
    if c == chr(96):
        n = len(re.match(r"\x60+", teks[i:])[0])
        j = teks.find(chr(96) * n, i + n)
        return j + n if j >= 0 else None
    if (c == '"' and not isi) or (c == "$" and isi):
        j = i + 1
        while j < len(teks):
            if teks[j] == "\\":
                j += 2
            elif teks[j] == c:
                return j + 1
            else:
                j += 1
        return None
    return i


def akhir_kurung_typst(teks, awal):
    """Cocokkan kurung kode, sambil memperlakukan (...) di [isi] sebagai teks."""
    pasangan = {"(": ")", "[": "]", "{": "}"}
    if not 0 <= awal < len(teks) or teks[awal] not in pasangan:
        return None
    stack = [(pasangan[teks[awal]], teks[awal] == "[")]
    i = awal + 1
    while i < len(teks):
        tutup, isi = stack[-1]
        j = _lewati_literal_typst(teks, i, isi)
        if j is None:
            return None
        if j != i:
            i = j
            continue
        c = teks[i]
        if isi:
            if c == "]":
                stack.pop()
            elif c == "[":
                stack.append(("]", True))
            elif c == "#":
                # #link(...), #table(...), #(expr), #{code}; #strong[isi] tetap markup.
                m = re.match(r"#(?:[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*)?\s*([({])",
                             teks[i:])
                if m:
                    stack.append((pasangan[m[1]], False))
                    i += m.end()
                    continue
            # Tanda kutip, ) dan } dalam teks sel bukan penutup ekspresi kode.
        elif c in pasangan:
            stack.append((pasangan[c], c == "["))
        elif c in ")]}":
            if c != tutup:
                return None
            stack.pop()
        if not stack:
            return i
        i += 1
    return None


def argumen_typst(teks, buka, akhir):
    """Rentang argumen tingkat terluar; koma di sel, fungsi, dan string diabaikan."""
    hasil, start, i = [], buka + 1, buka + 1
    while i < akhir:
        j = _lewati_literal_typst(teks, i)
        if j is None:
            return None
        if j != i:
            i = j
            continue
        c = teks[i]
        if c in "([{":
            j = akhir_kurung_typst(teks, i)
            if j is None or j >= akhir:
                return None
            i = j + 1
            continue
        if c == ",":
            hasil.append((start, i + 1))
            start = i + 1
        i += 1
    hasil.append((start, akhir))
    return hasil


def buka_figure_tabel(teks):
    """Pandoc 3.1 juga menulis figure(table) tanpa kind: table."""
    edits = []
    jumlah = 0
    for m in re.finditer(r"#figure\s*\(", teks):
        buka = teks.index("(", m.start())
        akhir = akhir_kurung_typst(teks, buka)
        if akhir is None:
            continue
        isi = teks[buka + 1:akhir]
        awal_tb = re.match(r"\s*align\(center\)\s*\[\s*(#table\s*\()", isi)
        if not awal_tb:
            continue
        p = awal_tb.start(1)
        buka_tb = isi.index("(", p)
        q = akhir_kurung_typst(isi, buka_tb)
        if q is None or not re.fullmatch(r"\s*\]\s*,?\s*(?:kind:\s*table\s*,?\s*)?", isi[q + 1:]):
            continue
        # Hapus hanya pembungkusnya: edit tabel induk dan anak tidak bertumpuk.
        edits.extend([(m.start(), buka + 1 + p, ""),
                      (buka + 1 + q + 1, akhir + 1, "")])
        jumlah += 1
    for p, q, pengganti in sorted(edits, reverse=True):
        teks = teks[:p] + pengganti + teks[q:]
    return teks, jumlah


def gaya_tabel_typst(teks):
    """Hapus hanya argumen gaya table; struktur yang tidak dikenali dibiarkan utuh."""
    hapus = []
    for m in re.finditer(r"#table\s*\(", teks):
        buka = teks.index("(", m.start())
        akhir = akhir_kurung_typst(teks, buka)
        if akhir is None:
            continue
        args = argumen_typst(teks, buka, akhir)
        if args is None:
            continue
        for a, z in args:
            if re.match(r"\s*(?:align|inset)\s*:", teks[a:z]):
                hapus.append((a, z))
    # Penghapusan bersifat lokal. Gabungkan rentang bertumpuk agar nested table
    # tidak mengubah indeks sumber atau menimpa hasil perapian tabel induknya.
    gabung = []
    for a, z in sorted(set(hapus)):
        if gabung and a <= gabung[-1][1]:
            gabung[-1] = (gabung[-1][0], max(z, gabung[-1][1]))
        else:
            gabung.append((a, z))
    for a, z in reversed(gabung):
        teks = teks[:a] + teks[z:]
    return teks


def batas_tinggi(nama="BATAS-TINGGI"):
    """FIX-BATAS-TEMPLATE: BATAS-TINGGI (tinggi awal gambar tinggi) / BATAS-TINGGI-MAKS (tinggi
    maksimum yang boleh dicoba looping) dibaca dari template_jurnal.typ."""
    try:
        with open(os.path.join(BASE, "template_jurnal.typ"), encoding="utf-8") as f:
            isi = f.read()
        m = re.search(r"#let {} = ([\d.]+)pt".format(re.escape(nama)), isi)
        if m:
            return float(m.group(1))
        return batas_tinggi() if nama != "BATAS-TINGGI" else 480.0
    except OSError:
        return 480.0


def ukuran_gambar(panggil):
    """FIX-OPTIMASI-SKALA: (lebar, tinggi) tampil gambar raster pada skala 1 (pt), aturan sama
    dengan #gambar di template (lebar slot / lebar-tampil; bila lebih tinggi dari BATAS-TINGGI,
    tinggi = BATAS-TINGGI dan lebar menyesuaikan). None bila bukan gambar raster (tabel, chart)
    atau ukuran piksel tak terbaca."""
    if not panggil.startswith("#gambar(") or "chart_render_" in panggil:
        return None
    m = re.search(r'#gambar\("([^"]+)"', panggil)
    try:
        from PIL import Image
        with Image.open(os.path.join(BASE, m.group(1).lstrip("/"))) as im:
            wpx, hpx = im.size
    except Exception:
        return None
    lebar = 442.2 if objek_penuh(panggil) else LEBAR_KOLOM_PT
    mt = re.search(r"lebar-tampil: ([\d.]+)pt", panggil)
    if mt:
        lebar = min(lebar, float(mt.group(1)))
    tinggi = lebar * hpx / float(wpx)
    batas = batas_tinggi()
    if tinggi > batas:
        return batas * wpx / float(hpx), batas, tinggi, wpx
    return lebar, tinggi, tinggi, wpx








def objek_luber(pos, teks=""):
    """FIX-OBJEK-LUBER: objek (gambar/tabel utuh) yang ujung bawahnya melewati dasar area teks,
    mis. float satu kolom yang terlalu tinggi untuk sisa kolom di bawah float selebar halaman.
    Hasil: daftar (nomor objek, halaman, kelebihan pt)."""
    hasil = []
    for k, v in (pos or {}).items():
        if not k.startswith("objek-info-"):
            continue
        nomor_str = k[len("objek-info-"):]
        try:
            nomor_int = int(nomor_str)
        except ValueError:
            nomor_int = None
        if nomor_int is not None and teks and objek_frozen_teks(teks, nomor_int):
            continue
        nilai, hal, x, y = v
        try:
            tinggi = float(nilai[1])
        except (TypeError, ValueError, IndexError):
            continue
        if tinggi <= 0:
            continue                         # tabel terpotong (tinggi tak diukur)
        lebih = y + tinggi - (841.89 - 79.0)
        if lebih > 3:
            hasil.append((nomor_int if nomor_int is not None else int(nomor_str), hal, lebih))
    return hasil


def baris_luber(pdf):
    """Baseline terlihat dan bbox glyph nyata; teks di bawah footer tetap diperiksa."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    hasil = []
    with fitz.open(pdf) as doc:
        for h, page in enumerate(doc, 1):
            garis = [d["rect"].y0 for d in page.get_drawings()
                     if d["rect"].width > 400 and d["rect"].height < 2
                     and 700 < d["rect"].y0 < 780]
            # Garis tabel selebar halaman dapat berada di area ini juga.
            # Footer adalah garis penuh paling bawah, bukan garis kepala tabel.
            y_footer = max(garis) if garis else None
            n = 0
            for line in baris_narasi_terlihat(pdf, h):
                if any(s["origin"][1] > 760.2 + ALIGN_EPS or
                       (y_footer is not None and s["bbox"][3] > y_footer - 1.2)
                       for s in line["spans"]):
                    n += 1
            if n:
                hasil.append((h, n))
    return hasil

def isi_bawah(pdf, h):
    """FIX-KAKI-AMAN: ujung bawah isi halaman h (teks bukan footer, gambar, garis/gambar vektor)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    with fitz.open(pdf) as doc:              # FIX-MEMORI-FITZ
        pg = doc[h - 1]
        bawah = 0.0
        for b in pg.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                t = "".join(x["text"] for x in l["spans"]).strip()
                if t and l["bbox"][1] > 100 and not re.search(r"^\d+\s*\||\|\s*\d+$", t):
                    bawah = max(bawah, l["bbox"][3])
        for im in pg.get_image_info():
            bawah = max(bawah, im["bbox"][3])
        for d in pg.get_drawings():
            r = d["rect"]
            if r.y1 > 105 and not (r.height < 2 and 764 < r.y0 < 767):    # bukan garis footer
                bawah = max(bawah, r.y1)
        return bawah


def halaman_kosong_bawah(pdf, batas=33.0):
    """FIX-RUANG-KOSONG: halaman (selain yang terakhir) yang isinya (teks, gambar, garis tabel)
    berakhir > batas pt di atas dasar area teks -> ruang kosong yang seharusnya diisi teks.
    Hasil: daftar (halaman, kosong pt)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    hasil = []
    flags_blk = fitz.TEXTFLAGS_BLOCKS & ~fitz.TEXT_PRESERVE_IMAGES
    with fitz.open(pdf) as doc:              # FIX-MEMORI-FITZ
        for i in range(doc.page_count - 1):
            pg = doc[i]
            bawah = 0.0
            for b in pg.get_text("blocks", flags=flags_blk):
                if 100 < b[1] < 762 and b[3] < 765:
                    bawah = max(bawah, b[3])
            for im in pg.get_image_info():
                if im["bbox"][3] < 765:
                    bawah = max(bawah, im["bbox"][3])
            for d in pg.get_drawings():
                r = d["rect"]
                if 105 < r.y1 < 764:
                    bawah = max(bawah, r.y1)
            kosong = 762.0 - bawah
            if bawah and kosong > batas:
                hasil.append((i + 1, kosong))
    return hasil


def lubang_kolom(pdf, pos, penuh_obj, teks=""):
    """FIX-LUBANG-KOLOM: kolom yang berakhir > 2 baris (22 pt) di atas kolom lain pada halaman
    yang sama, mis. float di puncak kolom yang mendorong teks ke kolom sebelah sehingga kolom
    lain berlubang. Footer (y > 740) dan objek selebar halaman dikecualikan.
    Hasil: daftar dict(hal, kol, selisih); kol 0=kiri/1=kanan = kolom yang lebih pendek."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    hasil = []
    akhir_ref = halaman_akhir_referensi(pos, teks)
    rentang_beku = []
    if teks:
        for n_pm in pm_beku_teks(teks):
            v_pm = pos.get("pm-{}".format(n_pm))
            if v_pm:
                ve_pm = pos.get("frag-akhir-{}-0".format(n_pm))
                rentang_beku.append((v_pm[1], 0 if v_pm[2] < 300 else 1,
                                     v_pm[3] - 2.0, (ve_pm[3] + 13.0) if ve_pm else v_pm[3] + 50.0))
        for lbl, v_ob in pos.items():
            mo = re.fullmatch(r"objek-info-(\d+)", lbl)
            if not mo or not objek_frozen_teks(teks, int(mo[1])) or penuh_obj.get(int(mo[1])):
                continue
            ak_ob = pos.get("objek-akhir-{}".format(mo[1]))
            tg_ob = float(v_ob[0][1]) if isinstance(v_ob[0], (tuple, list)) and len(v_ob[0]) > 1 else 0.0
            rentang_beku.append((v_ob[1], 0 if v_ob[2] < 300 else 1,
                                 v_ob[3] - 2.0, (ak_ob[3] + 2.0) if ak_ob else v_ob[3] + tg_ob + 2.0))
    with fitz.open(pdf) as doc:              # FIX-MEMORI-FITZ
        for i, pg in enumerate(doc, 1):
            if i == doc.page_count:                 # halaman terakhir: kolom kanan boleh pendek
                continue
            # rentang y objek selebar halaman: isi objek tidak dihitung per kolom
            larangan = []
            for k, isi_penuh in penuh_obj.items():
                if not isi_penuh:
                    continue
                info = pos.get("objek-info-{}".format(k))
                if not info:
                    continue
                akhir = pos.get("objek-akhir-{}".format(k))
                h0 = info[1]
                h1 = akhir[1] if akhir else h0
                if not (h0 <= i <= h1):
                    continue
                # FIX-LUBANG-LINTAS: halaman lanjutan objek selebar halaman juga dikecualikan;
                # sel tabel selebar halaman tidak boleh dihitung sebagai kolom kiri/kanan.
                y0 = info[3] if i == h0 else 0.0
                y1 = akhir[3] if akhir and i == h1 else 762.0
                larangan.append((y0 - 1.0, y1 + 1.0))

            larangan_gabung = []
            for a, b in sorted(larangan):
                if larangan_gabung and a <= larangan_gabung[-1][1]:
                    larangan_gabung[-1] = (larangan_gabung[-1][0], max(b, larangan_gabung[-1][1]))
                else:
                    larangan_gabung.append((a, b))

            cur = 100.0
            if i == 1 and pos.get("pm-1") and pos["pm-1"][1] == 1:
                cur = max(cur, pos["pm-1"][3] - 2.0)
            segmen = []
            for a, b in larangan_gabung:
                if a > cur + 1.0:
                    segmen.append((cur, min(a, 740.0)))
                cur = max(cur, b)
                if cur >= 740.0:
                    break
            if cur < 740.0 - 1.0:
                segmen.append((cur, 740.0))

            def dibekukan(kol, y0):
                return any(rh == i and rkol == kol and ya <= y0 <= yb for rh, rkol, ya, yb in rentang_beku)

            item_kol = []
            for bl in blok_teks(pdf, i):
                for ln in bl.get("lines", []):
                    if not ln.get("spans") or not "".join(s["text"] for s in ln["spans"]).strip():
                        continue
                    x0, y0, x1, y1 = ln["bbox"]
                    if y0 < 100 or y0 > 740:
                        continue
                    kol_i = 0 if x1 <= 300 else (1 if x0 >= 290 else -1)
                    if kol_i >= 0 and not dibekukan(kol_i, y0):
                        item_kol.append((kol_i, y0, y1))
            for im in pg.get_image_info():
                x0, y0, x1, y1 = im["bbox"]
                if y0 < 100 or y0 > 740 or (x1 - x0) > 400:
                    continue
                kol_i = 0 if x1 <= 300 else (1 if x0 >= 290 else -1)
                if kol_i >= 0 and not dibekukan(kol_i, y0):
                    item_kol.append((kol_i, y0, y1))

            terparah = {}
            for seg_a, seg_b in segmen:
                bawah = {0: None, 1: None}
                atas_min = {0: None, 1: None}
                for kol_i, y0, y1 in item_kol:
                    if not (seg_a <= y0 <= seg_b):
                        continue
                    y1_seg = min(y1, seg_b) if seg_b < 740.0 else y1
                    bawah[kol_i] = max(bawah[kol_i] or 0.0, y1_seg)
                    atas_min[kol_i] = min(atas_min[kol_i], y0) if atas_min[kol_i] is not None else y0
                if bawah[0] is not None and bawah[1] is not None:
                    selisih = abs(bawah[0] - bawah[1])
                    kol_pendek = 0 if bawah[0] < bawah[1] else 1
                    if kol_pendek == 1 and i in akhir_ref:
                        continue
                    if selisih > TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS and selisih > terparah.get(kol_pendek, 0.0):
                        terparah[kol_pendek] = selisih
                elif larangan and (bawah[0] is not None) != (bawah[1] is not None):
                    ada = 0 if bawah[0] is not None else 1
                    kol_pendek = 1 - ada
                    if kol_pendek == 1 and i in akhir_ref:
                        continue
                    tinggi_isi = bawah[ada] - max(seg_a, 102.4)
                    if (bawah[ada] - max(seg_a, 102.4, atas_min[ada]) > TOLERANSI_OBJEK_PENUH_PT + ALIGN_EPS
                            and tinggi_isi > terparah.get(kol_pendek, 0.0)):
                        terparah[kol_pendek] = tinggi_isi
            for kol_p, sel in sorted(terparah.items()):
                hasil.append(dict(hal=i, kol=kol_p, selisih=sel))
    return hasil


def nilai_tata_letak(teks, body_path, workdir, penuh_obj, skala):
    """Penilaian pencarian; hanya lulus_ketat yang mengizinkan publikasi."""
    main = os.path.join(workdir, "main.typ")
    pdf = os.path.join(workdir, "_skala.pdf")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(teks)
    rc, _, err = jalankan([TYPST, "compile", "--root", BASE, "--font-path",
                            os.path.join(BASE, "fonts"), main, pdf])
    pos, _ = kueri_posisi(main) if rc == 0 else (None, "")
    if pos is None:
        return float("inf"), {}, None
    u = ukur_layout_ketat(pdf, pos, teks)
    if "did not converge" in err:
        u["masalah"].append("Kompilasi tidak konvergen")
        u["lulus"] = False
    teks_baru, daftar_h = sisipkan_kaki_naik(teks, u)
    teks_pakai = teks
    if daftar_h:
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(teks_baru)
        rc2, _, err2 = jalankan([TYPST, "compile", "--root", BASE, "--font-path",
                                os.path.join(BASE, "fonts"), main, pdf])
        pos2, _ = kueri_posisi(main) if rc2 == 0 else (None, "")
        u2 = ukur_layout_ketat(pdf, pos2, teks_baru) if pos2 else None
        if u2 and "did not converge" in err2:
            u2["masalah"].append("Kompilasi tidak konvergen")
            u2["lulus"] = False
        if u2 and len(u2["masalah"]) < len(u["masalah"]) and u2["halaman"] <= u["halaman"]:
            teks_pakai = teks_baru
            u = u2
            pos = pos2
        else:
            with open(body_path, "w", encoding="utf-8") as f:
                f.write(teks)
            jalankan([TYPST, "compile", "--root", BASE, "--font-path",
                      os.path.join(BASE, "fonts"), main, pdf])
    r = dict(kiri=sum(max(0, x["kurang"]) / 11 for x in u["kolom"] if x["kol"] == 0),
             kanan_lebih=sum(max(0, x["kurang"]) / 11 for x in u["kolom"]
                             if x["kol"] == 1 and x["hal"] != u["halaman"]),
             tunggal=len(u["tunggal"]), grid=len(u["grid"]),
             halaman=u["halaman"], ketat=u,
             kanan=0, kanan_akhir=0, pita=0, lubang=0,
             teks=teks_pakai)
    nilai = kunci_layout_ketat(u)
    return nilai[0] * 1000000 + nilai[1] * 1000 + nilai[2], r, pos






def empat_nol(r):
    return bool(r) and bool(r.get("ketat", {}).get("lulus"))



def sidik_layout(docx, meta):
    """Resep adalah kandidat untuk input dan metadata yang sama, bukan cache hasil QA."""
    import hashlib
    import zipfile
    # FIX-SIDIK-ISI: hash isi XML, bukan byte zip. Timestamp entri zip berbeda antara
    # normalisasi di api.php dan build.py walau isi naskah sama; hash byte membuat resep
    # yang sudah LULUS tidak terpakai di produksi.
    try:
        with zipfile.ZipFile(docx) as z:
            isi = b"".join(z.read(nama) for nama in
                           ("word/document.xml", "word/styles.xml", "word/numbering.xml")
                           if nama in z.namelist())
        sumber = hashlib.sha256(isi).hexdigest()
    except (OSError, zipfile.BadZipFile):
        with open(docx, "rb") as f:
            sumber = hashlib.sha256(f.read()).hexdigest()
    # _halaman_akhir dihitung dari PDF final; bukan bagian identitas naskah/form.
    identitas = {k: v for k, v in meta.items() if k != "_halaman_akhir"}
    metadata = hashlib.sha256(json.dumps(identitas, ensure_ascii=False,
                              sort_keys=True).encode("utf-8")).hexdigest()
    return dict(docx_sha256=sumber, meta_sha256=metadata)


def muat_resep_layout(docx, meta, docx_sidik=None):
    path = os.path.splitext(os.path.abspath(docx))[0] + ".layout.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("versi") != 1 or data.get("sidik") != sidik_layout(docx_sidik or docx, meta):
            return None
        if data.get("header_grid", 0) not in (-22, -11, 0, 11, 22):
            return None
        keputusan = {int(k): v for k, v in data["keputusan"].items()}
        if any(not isinstance(v, dict) or v.get("jenis") not in
               ("tempat", "atas", "bawah", "tunda", "kecil", "lanjut", "geser", "pindah_pita")
               for v in keputusan.values()):
            return None
        return dict(keputusan=keputusan, header=data.get("header_grid", 0),
                    potongan=[tuple(x) for x in data.get("potongan", [])],
                    akhir_seimbang=bool(data.get("akhir_seimbang", False)))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def simpan_resep_layout(docx, meta, keputusan, header, potongan, akhir_seimbang=False,
                        docx_sidik=None):
    path = os.path.splitext(os.path.abspath(docx))[0] + ".layout.json"
    data = dict(versi=1, sidik=sidik_layout(docx_sidik or docx, meta), keputusan=keputusan,
                header_grid=header, potongan=potongan,
                akhir_seimbang=bool(akhir_seimbang))
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)


def atur_header_grid(main, shift):
    if shift not in (-22, -11, 0, 11, 22):
        raise ValueError("Pergeseran header harus mengikuti grid 11 pt")
    with open(main, encoding="utf-8") as f:
        text = f.read()
    text, n = re.subn(r"^#show: artikel\.with\(cfg, meta(?:, header-grid: -?\d+pt)?\)$",
                     "#show: artikel.with(cfg, meta, header-grid: {}pt)".format(shift),
                     text, flags=re.M)
    if n != 1:
        raise ValueError("Pemanggilan template produksi tidak ditemukan")
    with open(main, "w", encoding="utf-8") as f:
        f.write(text)


def render_kandidat_layout(body, keputusan, potongan, akhir_seimbang=False):
    """Susun ulang posisi objek sambil mempertahankan semua titik putus terpilih."""
    text = susun_teks(body, keputusan, akhir_seimbang=akhir_seimbang)
    for spec in potongan:
        text = terapkan_potong(text, spec)
        if text is None:
            return None
    return text


def sela_kandidat(grup, batas):
    """Round-robin: setiap jenis operator mendapat kesempatan pada setiap generasi."""
    from itertools import zip_longest
    hasil = []
    for batch in zip_longest(*grup):
        for item in batch:
            if item is not None:
                hasil.append(item)
                if len(hasil) >= batas:
                    return hasil
    return hasil


def nilai_kandidat_layout(u, keputusan, header):
    buruk = [x for x in u["kolom"] if not kolom_sah(x, u["halaman"])]
    kiri_buruk = [x for x in buruk if x["kol"] == 0]
    kanan_11pt = sum(1 for x in u["kolom"]
                     if x["kol"] == 1 and x["hal"] != u["halaman"] and x["kurang"] > ALIGN_EPS)
    keras = len(u["grid"]) + len(u["tunggal"])
    keras += sum("meluber" in m or "melewati dasar" in m or "penanda" in m.lower()
                 or "SESUDAH judul bagian" in m or "Pita teks" in m or "terjepit" in m
                 or "Lubang kolom" in m or "kosong" in m
                 or "Kolom kanan lebih panjang" in m or "hanya berisi" in m
                 for m in u["masalah"])
    # FIX-KIRI-FATAL: kolom kiri pendek tidak boleh ditukar dengan kolom kanan yang
    # menggantung (aturan 2 lebih keras daripada kelonggaran kanan maks. 1 baris).
    keras += 999 * len(kiri_buruk)
    prefix = min((x["hal"] for x in buruk), default=u["halaman"] + 1) - 1
    # Pelanggaran wajib lebih penting daripada estetika; utamakan kolom kanan 0 pt (kanan_11pt == 0).
    return (keras, len(u["masalah"]), len(buruk),
            round(sum(abs(x["kurang"]) for x in buruk), 3),
            kanan_11pt, -prefix,
            u["halaman"], abs(header),
            round(sum(abs(v.get("skala", 1) - 1) for v in keputusan.values()), 4))


def spesifikasi_potong_layout(teks, pdf, pos, u):
    tidak_sah_set = {x["hal"] for x in u["kolom"] if not kolom_sah(x, u["halaman"])}
    for m_str in u.get("masalah", []):
        for h_m in re.findall(r"hlm(?: fisik)? (\d+)|Halaman (\d+)", m_str):
            h_val = int(h_m[0] or h_m[1])
            awal_c = HALAMAN_AWAL_CETAK[0] or 1
            if awal_c > 1 and h_val >= awal_c:
                h_val = h_val - awal_c + 1
            if 1 <= h_val <= u["halaman"]:
                tidak_sah_set.add(h_val)
    tidak_sah = sorted(tidak_sah_set)
    lain = sorted({x["hal"] for x in u["kolom"] if (x["kol"] == 1 and x["hal"] != u["halaman"] and x["kurang"] > ALIGN_EPS)} | {t["hal"] for t in u["tunggal"]})
    buruk = tidak_sah + [h for h in lain if h not in tidak_sah]
    for h in sorted({h for p in buruk[:2] for h in (p - 1, p) if h >= 1}):
        for m in (1, 2, 3):
            for kol in (0, 1):
                spec = cari_potong(pdf, pos, h, kol, m, teks)
                if spec:
                    yield spec


def pilih_kandidat(body_dasar, body_path, workdir, keputusan, kandidat, laporan, log, stat):
    """Beam bertahap: resep terverifikasi ulang, header, ukuran, posisi, dan potongan."""
    import copy
    import hashlib
    import time
    mulai = time.monotonic()
    maks = max(1, int(os.environ.get("ALIGN_MAX_EVAL", "160")))
    lebar = max(1, int(os.environ.get("ALIGN_BEAM", "3")))
    kuota = max(4, int(os.environ.get("ALIGN_PER_PARENT", "10")))
    main = os.path.join(workdir, "main.typ")
    pdf = os.path.join(workdir, "_skala.pdf")
    resep = stat.get("resep_layout")
    frontier = []
    if resep and set(resep["keputusan"]) == set(keputusan):
        frontier.append(dict(kp=copy.deepcopy(resep["keputusan"]), header=resep["header"],
                             cuts=list(resep["potongan"]), asal="resep",
                             akhir_seimbang=bool(resep.get("akhir_seimbang", False))))
    frontier.append(dict(kp=copy.deepcopy(keputusan), header=0, cuts=[], asal="awal",
                         akhir_seimbang=False))
    # FIX-AKHIR-SEIMBANG-CANDIDATE: segmen terakhir dibiarkan seimbang (dua kolom) bila
    # sisa naskah tidak cukup untuk satu kolom penuh. Dicoba berdampingan dengan versi
    # kolom-lanjut; QA akhir-seimbang memutuskan mana yang sah.
    if "#kolom[" in body_dasar or "#kolom-lanjut[" in body_dasar:
        frontier.append(dict(kp=copy.deepcopy(keputusan), header=0, cuts=[],
                             asal="akhir-seimbang", akhir_seimbang=True))
    frontier += [dict(kp=copy.deepcopy(kp), header=0, cuts=[], asal="penempatan",
                      akhir_seimbang=False)
                 for _, kp in kandidat[:lebar]]
    dilihat, population, terbaik = set(), [], None
    evaluasi, hits, generasi = 0, 0, 0
    operators = {}

    def node(kp, header, cuts, asal, akhir_seimbang=False):
        return dict(kp=copy.deepcopy(kp), header=header, cuts=list(cuts), asal=asal,
                    akhir_seimbang=akhir_seimbang)

    while frontier and evaluasi < maks and time.monotonic() - mulai < BATAS_WAKTU[0]:
        generasi += 1
        for state in frontier:
            if evaluasi >= maks or time.monotonic() - mulai >= BATAS_WAKTU[0]:
                break
            text = render_kandidat_layout(body_dasar, state["kp"], state["cuts"],
                                          state.get("akhir_seimbang", False))
            if text is None:
                continue
            key = (state["header"], hashlib.sha256(text.encode("utf-8")).hexdigest())
            if key in dilihat:
                hits += 1
                continue
            dilihat.add(key)
            atur_header_grid(main, state["header"])
            _, ri, pos = nilai_tata_letak(text, body_path, workdir, {}, {})
            evaluasi += 1
            operators[state["asal"]] = operators.get(state["asal"], 0) + 1
            if not ri:
                continue
            text = ri.get("teks", text)
            u = ri["ketat"]
            tambahan, _, _ = qa_tata_letak(
                dict(stat, body_dasar=body_dasar, keputusan=state["kp"],
                     kolom_kurang=u["kolom"], jumlah_halaman=u["halaman"], pdf_qa=pdf),
                pos, workdir, HALAMAN_AWAL_CETAK[0], "Times New Roman", cek_font=False)
            penuh = {k: objek_penuh_teks(text, k) for k in state["kp"]}
            pita_list = pita_kecil(pdf, pos, penuh)
            for p in pita_list:
                if p["letak"] == "tengah":
                    tambahan.append("Pita teks terjepit {} baris di antara objek hlm fisik {} (min. {})".format(
                        p["baris"], p["hal"], PITA_TENGAH_MIN))
                else:
                    tambahan.append("Pita teks {} baris hlm fisik {}".format(p["baris"], p["hal"]))
            # FIX-RUANG-KOSONG-SEARCH: ruang kosong > 33 pt di bawah isi halaman juga
            # harus dinilai saat pencarian, bukan hanya pada QA akhir.
            try:
                for h_k, k_k in halaman_kosong_bawah(pdf):
                    tambahan.append("Halaman {} kosong {:.0f} pt di bagian bawah".format(h_k, k_k))
            except Exception:       # PyMuPDF punya FileNotFoundError sendiri (bukan OSError)
                pass        # kandidat uji tanpa PDF (unit test dengan pengukuran tiruan)
            u["masalah"] = list(dict.fromkeys(u["masalah"] + tambahan))
            u["lulus"] = not u["masalah"]
            state.update(text=text, u=u, pos=pos, expanded=False,
                         score=nilai_kandidat_layout(u, state["kp"], state["header"]))
            population.append(state)
            prev_best_cnt = len(terbaik["u"]["masalah"]) if terbaik else 999
            if terbaik is None or state["score"] < terbaik["score"]:
                terbaik = state
                log.append("Pencarian: evaluasi {}, {} masalah, {} halaman; operator {}, header {} pt".format(
                    evaluasi, len(u["masalah"]), u["halaman"], state["asal"], state["header"]))
            kanan_11 = sum(1 for x in u["kolom"]
                           if x["kol"] == 1 and x["hal"] != u["halaman"]
                           and not x.get("pengecualian_target") and not x.get("penuh_objek")
                           and x["kurang"] > ALIGN_EPS)
            if u["lulus"] and kanan_11 == 0:
                break
            if u["lulus"] and "eval_lulus_pertama" not in stat:
                stat["eval_lulus_pertama"] = evaluasi
            # PDF ini masih milik kandidat ini: simpan spesifikasi potong sekarang.
            state["cuts_next"] = list(spesifikasi_potong_layout(text, pdf, pos, u))
            state["pita_moves"] = []
            for pt in pita_list:
                if pt["letak"] in ("atas", "tengah"):
                    k2 = pt.get("bawah")
                    if (k2 in state["kp"] and state["kp"][k2].get("jenis") in ("tempat", "geser")
                            and body_dasar[slice(*blok_objek(body_dasar, k2))].startswith("#tabel(")):
                        y_mulai_pt = 100.0
                        if pt["letak"] == "tengah" and pt.get("atas"):
                            I_atas = pos.get("objek-info-{}".format(pt["atas"]))
                            A_atas = pos.get("objek-akhir-{}".format(pt["atas"]))
                            if A_atas and A_atas[1] == pt["hal"]:
                                y_mulai_pt = A_atas[3]
                            elif I_atas and I_atas[1] == pt["hal"]:
                                tg_atas = float(I_atas[0][1]) if isinstance(I_atas[0], (list, tuple)) and len(I_atas[0]) > 1 else 0.0
                                y_mulai_pt = I_atas[3] + tg_atas
                        info_pita = cari_pita_atas_tabel(pdf, pos, pt["hal"], k2, text, y_mulai=y_mulai_pt)
                        if info_pita:
                            kp_pita = copy.deepcopy(state["kp"])
                            kp_pita[k2] = dict(kp_pita[k2], jenis="pindah_pita",
                                               mode=info_pita[0], n_pm=info_pita[1],
                                               teks_baris=info_pita[2])
                            state["pita_moves"].append(kp_pita)
            if terbaik is state and not u["lulus"] and len(u["masalah"]) <= 3 and len(u["masalah"]) < prev_best_cnt:
                break
        if terbaik and terbaik["u"]["lulus"]:
            kanan_terbaik = sum(1 for x in terbaik["u"]["kolom"]
                                if x["kol"] == 1 and x["hal"] != terbaik["u"]["halaman"]
                                and not x.get("pengecualian_target") and not x.get("penuh_objek")
                                and x["kurang"] > ALIGN_EPS)
            if kanan_terbaik == 0 or (evaluasi - stat.get("eval_lulus_pertama", evaluasi)) >= 28:
                break
        unexp = sorted((p for p in population if not p["expanded"]), key=lambda p: p["score"])
        parents = unexp[:lebar]
        if lebar >= 3 and len(parents) == lebar and len(parents[0]["u"]["masalah"]) > 1:
            hdrs = {p["header"] for p in parents}
            if len(hdrs) == 1:
                alt = next((p for p in unexp[lebar:] if p["header"] not in hdrs), None)
                if alt is not None:
                    parents[-1] = alt
        batches = []
        for parent in parents:
            parent["expanded"] = True
            kp, cuts, header = parent["kp"], parent["cuts"], parent["header"]
            akhir = parent.get("akhir_seimbang", False)
            cut_moves, size_by_k, place_by_k = [], {}, {}
            for spec in parent.get("cuts_next", []):
                if not any(old[1] == spec[1] for old in cuts):
                    cut_moves.append(node(kp, header, cuts + [spec], "potong", akhir))
            # Mencabut satu potongan adalah operator sah; QA mengecek paragraf lagi.
            for i in range(len(cuts)):
                cut_moves.append(node(kp, header, cuts[:i] + cuts[i + 1:], "gabung", akhir))
            for kp2 in calon_objek_ketat(body_dasar, kp, parent["pos"], parent["u"]):
                k_ubah = next((k for k in kp if kp2[k] != kp[k]), None)
                ukuran = any(kp2[k].get("skala") != kp[k].get("skala") for k in kp)
                by_k = size_by_k if ukuran else place_by_k
                by_k.setdefault(k_ubah, []).append(
                    node(kp2, header, cuts, "ukuran" if ukuran else "posisi", akhir))
            size_moves = sela_kandidat(list(size_by_k.values()), kuota)
            pita_nodes = [node(kp_p, header, cuts, "posisi", akhir)
                          for kp_p in parent.get("pita_moves", [])]
            place_moves = pita_nodes + sela_kandidat(list(place_by_k.values()), kuota)
            header_moves = [node(kp, h, cuts, "header", akhir) for h in (11, -11, 22, -22, 0) if h != header]
            h_akhir = parent["u"]["halaman"]
            if (("#kolom[" in body_dasar or "#kolom-lanjut[" in body_dasar)
                    and (any(x["hal"] == h_akhir and not kolom_sah(x, h_akhir) for x in parent["u"]["kolom"])
                         or any(t["hal"] == h_akhir for t in parent["u"]["tunggal"]))):
                header_moves.insert(0, node(kp, header, cuts, "akhir-seimbang", not akhir))
            kuota_p = kuota + 6 if len(parent["u"]["masalah"]) <= 2 else kuota
            batches.append(sela_kandidat([header_moves, size_moves, place_moves, cut_moves], kuota_p))
        if len(batches) > 1 and parents[0]["score"] < parents[1]["score"]:
            prio_n = len(batches[0]) if len(parents[0]["u"]["masalah"]) <= 2 else 10
            prio = batches[0][:min(prio_n, kuota * lebar)]
            sisa = kuota * lebar - len(prio)
            frontier = prio + (sela_kandidat([batches[0][prio_n:]] + batches[1:], sisa) if sisa > 0 else [])
        else:
            frontier = sela_kandidat(batches, kuota * lebar)
    if terbaik is None:
        raise RuntimeError("Tidak ada kandidat yang berhasil dikompilasi dan diukur")
    atur_header_grid(main, terbaik["header"])
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(terbaik["text"])
    stat["kolom_kurang"] = terbaik["u"]["kolom"]
    stat["layout_terpilih"] = dict(header_grid=terbaik["header"], potongan=terbaik["cuts"],
                                   akhir_seimbang=bool(terbaik.get("akhir_seimbang", False)))
    stat["alignment_pencarian"] = dict(
        evaluasi=evaluasi, generasi=generasi, cache_hits=hits, operator=operators,
        lulus=terbaik["u"]["lulus"], batas_evaluasi=maks, batas_detik=BATAS_WAKTU[0],
        header_grid=terbaik["header"],
        alasan="lulus" if terbaik["u"]["lulus"] else "belum ditemukan dalam batas pencarian")
    log.append("Pencarian selesai: {} evaluasi, {} generasi; {}".format(
        evaluasi, generasi, stat["alignment_pencarian"]["alasan"]))
    return terbaik["kp"]


def potong_target_terverifikasi(teks, pdf, pos, ukuran, hal, kol, delta, kompilasi):
    """Colbreak sah hanya jika PDF baru membuktikan baseline berkurang dan teks pindah."""
    xs = [x for x in ukuran["kolom"] if x["hal"] == hal and x["kol"] == kol]
    if len(xs) != 1 or xs[0]["kosong"]:
        return None
    before = xs[0]
    m = int(round((delta - before["kurang"]) / 11.0))
    if m <= 0 or abs(before["kurang"] + m * 11.0 - delta) > ALIGN_EPS:
        return None
    spec = cari_potong(pdf, pos, hal, kol, m, teks)
    if not spec:
        return None
    calon = terapkan_potong(teks, spec)
    if not calon or calon.count("#colbreak()") != teks.count("#colbreak()") + 1:
        return None
    u, p = kompilasi(calon)
    if not u or not p:
        return None
    after = next((x for x in u["kolom"] if x["hal"] == hal and x["kol"] == kol), None)
    label = "pm-{}".format(spec[1]) if spec[0] == "putus" else "frag-awal-{}-1".format(spec[1])
    moved = p.get(label)
    if (after is None or after["kosong"]
            or abs(after["akhir_baseline"] - (before["akhir_baseline"] - m * 11.0)) > ALIGN_EPS
            or abs(after["kurang"] - delta) > ALIGN_EPS
            or moved is None or (moved[1], 0 if moved[2] < 300 else 1) <= (hal, kol)):
        return None
    bukti = dict(kolom=kol, baris_dipindah=m, paragraf=spec[1],
                 baseline_sebelum=before["akhir_baseline"],
                 baseline_sesudah=after["akhir_baseline"], halaman_tujuan=moved[1])
    return calon, u, p, bukti


def perbaiki_halaman_target(body_path, workdir, laporan, keputusan):
    """Pengecualian eksplisit pengguna: hanya naskah 549, halaman cetak 581/595."""
    with open(os.path.join(workdir, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("_artikel_id") != TARGET_ARTIKEL:
        return
    awal = int(meta["terbitan"]["halaman_awal"])
    main, pdf = os.path.join(workdir, "main.typ"), os.path.join(workdir, "_target.pdf")
    with open(body_path, encoding="utf-8") as f:
        teks = f.read()
    log = laporan.setdefault("log_objek", [])

    def ukur(t):
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(t)
        rc, _, err = jalankan([TYPST, "compile", "--root", BASE, "--font-path",
                                os.path.join(BASE, "fonts"), main, pdf])
        if rc or "did not converge" in err:
            return None, None
        p, _ = kueri_posisi(main)
        return (ukur_layout_ketat(pdf, p, t), p) if p else (None, None)

    def sisi(u, h):
        return sorted((x for x in u["kolom"] if x["hal"] == h), key=lambda x: x["kol"])

    def beres(u, h):
        xs = sisi(u, h)
        return len(xs) == 2 and all(not x["kosong"] and (
            abs(x["kurang"]) <= ALIGN_EPS or x.get("pengecualian_target")) for x in xs)

    def aman(u, sebelum, terselesaikan):
        if not u or u["halaman"] > sebelum["halaman"] or u["tunggal"] or u["grid"]:
            return False
        if any("meluber" in m or "melewati dasar" in m for m in u["masalah"]):
            return False
        return all(beres(u, h) for h in terselesaikan)

    selesai = set()
    ringkas = []
    for nomor in TARGET_HALAMAN:
        h = nomor - awal + 1
        baseline, pos = ukur(teks)
        if not baseline or not 1 <= h <= baseline["halaman"]:
            laporan.setdefault("masalah", []).append("Target hlm {} tidak dapat diukur".format(nomor))
            continue
        if beres(baseline, h):
            selesai.add(h)
            naik = sisi(baseline, h)[0].get("footer_naik", 0)
            ringkas.append(dict(halaman=nomor, cara="kolom_sejajar_footer" if naik else "penuh", footer_naik=naik))
            continue

        # A: perubahan 1% pada gambar halaman ini/sebelumnya. Semua halaman diukur ulang.
        variasi = []
        for label, v in pos.items():
            if not label.startswith("objek-info-") or v[1] not in (h - 1, h):
                continue
            k = int(label.rsplit("-", 1)[1])
            match = re.search(r'^#gambar\([^\n]*\bnomor: {}\b[^\n]*$'.format(k), teks, re.M)
            if not match or "chart_render_" in match[0]:
                continue
            sk = re.search(r"\bskala: ([\d.]+)", match[0])
            asal = float(sk[1]) if sk else 1.0
            for faktor in (0.99, 1.01):
                skala = round(asal * faktor, 3)
                if not 0.7 <= skala <= 1.2:
                    continue
                panggil = re.sub(r",\s*skala: [\d.]+", "", match[0])
                panggil = panggil.replace("nomor:", "skala: {:.3f}, nomor:".format(skala), 1)
                variasi.append((teks[:match.start()] + panggil + teks[match.end():], k, skala))
        diperbaiki = False
        for calon, k, skala in variasi:
            u, _ = ukur(calon)
            if aman(u, baseline, selesai) and beres(u, h) and len(u["masalah"]) <= len(baseline["masalah"]):
                teks = calon
                keputusan[k]["skala"] = skala
                selesai.add(h)
                ringkas.append(dict(halaman=nomor, cara="gambar_1_persen", footer_naik=0))
                log.append("Target hlm {}: penuh dengan gambar {} skala {:.3f}".format(nomor, k, skala))
                diperbaiki = True
                break
        if diperbaiki:
            continue

        # B: potong sisi yang lebih panjang, min. dua baris tiap fragmen, maksimum dua slot.
        current, pos = ukur(teks)
        pasangan = sisi(current, h) if current else []
        if len(pasangan) != 2 or any(x["kosong"] for x in pasangan):
            laporan.setdefault("masalah", []).append("Target hlm {} tidak memiliki dua baseline teks".format(nomor))
            continue
        delta = max(x["kurang"] for x in pasangan)
        delta = round(delta / 11.0) * 11.0
        calon = teks
        bukti_potong = []
        if delta in (11.0, 22.0):
            for _ in range(4):
                u, p = ukur(calon)
                if not u:
                    break
                xs = sisi(u, h)
                if baseline_target_sama(xs, delta):
                    break
                if len(xs) != 2 or any(x["kosong"] for x in xs):
                    break
                x = min(xs, key=lambda x: x["kurang"])
                hasil = potong_target_terverifikasi(calon, pdf, p, u, h, x["kol"], delta, ukur)
                if hasil is None:
                    log.append("Target hlm {}: colbreak tidak terbukti memindahkan teks; footer ditolak".format(nomor))
                    break
                calon, _, _, bukti = hasil
                bukti_potong.append(bukti)
            u, _ = ukur(calon)
            if u and baseline_target_sama(sisi(u, h), delta):
                kepala = '#import "/template_jurnal.typ": *\n'
                calon = re.sub(r"^#kaki-naik\({}, [\d.]+pt\)\n".format(h), "", calon, flags=re.M)
                calon = calon.replace(kepala, kepala + "#kaki-naik({}, {:.1f}pt)\n".format(h, delta), 1)
                u, p_akhir = ukur(calon)
                # Potongan dapat menambah dua baris ke halaman berikut. Serap aliran
                # itu dengan gambar terdekat, dicoba per 1%, tanpa mengubah tipografi.
                if not (aman(u, baseline, selesai) and beres(u, h)):
                    terserap = False
                    for label, v in (p_akhir or {}).items():
                        if not label.startswith("objek-info-") or v[1] not in (h, h + 1):
                            continue
                        k = int(label.rsplit("-", 1)[1])
                        match = re.search(r'^#gambar\([^\n]*\bnomor: {}\b[^\n]*$'.format(k), calon, re.M)
                        if not match or "chart_render_" in match[0]:
                            continue
                        sk = re.search(r"\bskala: ([\d.]+)", match[0])
                        asal = float(sk[1]) if sk else 1.0
                        ukuran = ukuran_gambar(match[0])
                        slot = 442.2 if objek_penuh(match[0]) else LEBAR_KOLOM_PT
                        for langkah in range(1, 13):
                            skala = round(asal * (1 - langkah * 0.01), 3)
                            if (not ukuran or skala < 0.6 or ukuran[0] * skala < 0.6 * slot
                                    or ukuran[3] * 72 / (ukuran[0] * skala) < 150):
                                continue
                            panggil = re.sub(r",\s*skala: [\d.]+", "", match[0])
                            panggil = panggil.replace("nomor:", "skala: {:.3f}, nomor:".format(skala), 1)
                            t2 = calon[:match.start()] + panggil + calon[match.end():]
                            u2, _ = ukur(t2)
                            if (aman(u2, baseline, selesai) and beres(u2, h)
                                    and len(u2["masalah"]) <= len(baseline["masalah"])):
                                calon, u = t2, u2
                                keputusan[k]["skala"] = skala
                                log.append("Target hlm {}: aliran diserap gambar {} skala {:.3f}".format(nomor, k, skala))
                                terserap = True
                                break
                        if terserap:
                            break
                if (aman(u, baseline, selesai) and beres(u, h)
                        and baseline_target_sama(sisi(u, h), delta)):
                    teks = calon
                    selesai.add(h)
                    ys = [x["akhir_baseline"] for x in sisi(u, h)]
                    ringkas.append(dict(halaman=nomor, cara="kolom_sejajar_footer", footer_naik=delta,
                                        baseline_kiri=ys[0], baseline_kanan=ys[1], potongan=bukti_potong))
                    log.append("Target hlm {}: baseline terlihat kiri {:.2f} = kanan {:.2f} pt; footer naik {:.0f} pt".format(nomor, *ys, delta))
                    continue
        laporan.setdefault("masalah", []).append("Target hlm {} belum berhasil diperbaiki".format(nomor))
        log.append("Target hlm {}: variasi gambar dan titik potong belum sah".format(nomor))
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(teks)
    laporan["perbaikan_target"] = ringkas



def perbaiki_akhir_referensi(body_path, workdir, laporan, keputusan, stat):
    """Geser fase aliran References lewat ukuran gambar; tidak melonggarkan halaman tengah."""
    main, pdf = os.path.join(workdir, "main.typ"), os.path.join(workdir, "_referensi.pdf")
    with open(body_path, encoding="utf-8") as f:
        sumber = kolom_akhir_tidak_seimbang(f.read())

    def ukur(teks):
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(teks)
        rc, _, err = jalankan([TYPST, "compile", "--root", BASE, "--font-path",
                                os.path.join(BASE, "fonts"), main, pdf])
        if rc or "did not converge" in err:
            return None, None
        pos, _ = kueri_posisi(main)
        return (ukur_layout_ketat(pdf, pos, teks), pos) if pos else (None, None)

    awal, pos = ukur(sumber)
    if not awal or awal["lulus"]:
        return
    refs = [int(m[1]) for m in re.finditer(r"#referensi\[#pm\((\d+)\)", sumber)]
    if not refs:
        return
    pertama = pos.get("pm-" + str(refs[0]))
    if not pertama:
        return
    # Fokus pada kasus kanan pendek di References, bukan menutupi masalah bagian lain.
    buruk = [x for x in awal["kolom"] if not kolom_sah(x, awal["halaman"])]
    if not buruk or any(x["kol"] != 1 or x["hal"] < pertama[1] for x in buruk):
        return
    gambar = sorted(((v[1], int(label.rsplit("-", 1)[1])) for label, v in pos.items()
                     if label.startswith("objek-info-") and v[1] <= pertama[1]), reverse=True)
    dicoba = 0
    for halaman_gambar, k in gambar:
        match = re.search(r'^#gambar\([^\n]*\bnomor: {}\b[^\n]*$'.format(k), sumber, re.M)
        if not match or "chart_render_" in match[0]:
            continue
        dicoba += 1
        ukuran = ukuran_gambar(match[0])
        if not ukuran:
            continue
        sk = re.search(r"\bskala: ([\d.]+)", match[0])
        asal = float(sk[1]) if sk else 1.0
        slot = 442.2 if objek_penuh(match[0]) else LEBAR_KOLOM_PT
        for langkah in range(1, 9):
            for arah in (-1, 1):
                skala = round(asal * (1 + arah * langkah / 100.0), 3)
                if (not 0.7 <= skala <= 1.2 or ukuran[0] * skala > slot + ALIGN_EPS
                        or ukuran[3] * 72 / (ukuran[0] * skala) < 150):
                    continue
                panggil = re.sub(r",\s*skala: [\d.]+", "", match[0])
                panggil = panggil.replace("nomor:", "skala: {:.3f}, nomor:".format(skala), 1)
                calon = sumber[:match.start()] + panggil + sumber[match.end():]
                u, pp = ukur(calon)
                if not u or not u["lulus"] or u["halaman"] > awal["halaman"]:
                    continue
                kp = {n: dict(v) for n, v in keputusan.items()}
                kp[k]["skala"] = skala
                extra, _, _ = qa_tata_letak(
                    dict(stat, keputusan=kp, kolom_kurang=u["kolom"],
                         jumlah_halaman=u["halaman"], pdf_qa=pdf),
                    pp, workdir, 1, "Times New Roman", cek_font=False)
                if extra:
                    continue
                keputusan[k]["skala"] = skala
                laporan["perbaikan_referensi"] = dict(objek=k, skala=skala, langkah_persen=arah * langkah)
                laporan.setdefault("log_objek", []).append(
                    "References dirapikan dengan gambar {} skala {:.3f}; semua kolom memenuhi batas".format(k, skala))
                return
        # Hanya dua gambar terakhir sebelum References; kompilasi dibatasi.
        if dicoba >= 2:
            break
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(sumber)


def qa_tata_letak(stat, pos, workdir, halaman_awal, font_cfg, cek_font=True):
    """FIX-QA-TATA-LETAK: (1) urutan objek vs docx lewat penanda; (2) kolom kiri menggantung;
    (3) font chart = font narasi 11 pt; (4) ringkasan per objek."""
    masalah, peringatan, ringkas = [], [], []
    body = stat.get("body_dasar", "")
    kp = stat.get("keputusan", {})
    label = stat.get("label", [])
    for k, hal, lebih in objek_luber(pos):           # FIX-OBJEK-LUBER
        nama = ((label[k - 1] if k - 1 < len(label) else "") or "Objek {}".format(k)).rstrip(".")
        masalah.append("{} melewati dasar halaman {} ({:.0f} pt)".format(nama, hal, lebih))
    def titik(lbl):
        v = pos.get(lbl)
        return (v[1], v[2], v[3]) if v else None
    # pita objek selebar halaman per halaman (untuk urutan baca objek satu kolom)
    penuh = {}
    for (k, _, _) in stat.get("peta_objek", []):
        blk = body[slice(*blok_objek(body, k))] if '"@K{}@"'.format(k) in body else ""
        penuh[k] = objek_penuh(blk)
    pita = {}
    for k, pn in penuh.items():
        t = titik("objek-info-{}".format(k))
        if pn and t:
            pita.setdefault(t[0], []).append(t[2])
    def kunci(t, kolom_obj):
        hal, x, y = t
        if not kolom_obj:
            return (hal, y)
        band = sum(1 for yy in pita.get(hal, []) if yy < y - 1)
        return (hal, band, 0 if x < 300 else 1, y)
    # FIX-URUTAN-ANTAROBJEK: gambar/tabel bernomor harus tampil berurutan (Figure 2 sebelum
    # Figure 3) menurut urutan baca (halaman, pita objek lebar, kolom, y)
    urut_o = []
    for (k, _, _) in stat.get("peta_objek", []):
        o = titik("objek-info-{}".format(k))
        lbl_k = (label[k - 1] if k - 1 < len(label) else "") or ""
        m_k = re.match(r"(Figure|Gambar|Table|Tabel)\s+(\d+)", lbl_k)
        if o and m_k:
            kol_k = not penuh.get(k, True)
            urut_o.append((m_k.group(1).lower()[:3], int(m_k.group(2)), kunci(o, kol_k),
                           lbl_k.rstrip("."), kol_k, o[0]))
    for jenis_o in {u[0] for u in urut_o}:
        seri = sorted((u for u in urut_o if u[0] == jenis_o), key=lambda u: u[1])
        for a, b in zip(seri, seri[1:]):
            # objek sejenis (sama-sama kolom / sama-sama lebar): urutan baca lengkap;
            # campuran: cukup tidak di halaman yang lebih awal
            salah = (b[2] < a[2]) if a[4] == b[4] else (b[5] < a[5])
            if salah:
                masalah.append("{} muncul sebelum {}".format(b[3], a[3]))
    for (k, n_sebelum, n_sesudah) in stat.get("peta_objek", []):
        nama = ((label[k - 1] if k - 1 < len(label) else "") or "Objek {}".format(k)).rstrip(".")   # FIX-QA-PESAN
        o = titik("objek-info-{}".format(k))
        jenis = kp.get(k, {}).get("jenis", "tempat")
        info = {"objek": nama.rstrip("."), "keputusan": jenis,
                "halaman": (halaman_awal + o[0] - 1) if o else None}
        if jenis == "kecil":
            info["skala"] = round(kp[k].get("skala", 1), 3)
        blk = body[slice(*blok_objek(body, k))] if '"@K{}@"'.format(k) in body else ""
        if blk.startswith("#gambar(") and "chart_render_" not in blk:
            info["catatan"] = "gambar raster: teks di dalamnya tidak diseragamkan otomatis"
        ringkas.append(info)
        if not o:
            masalah.append("{}: posisi tidak terbaca".format(nama))
            continue
        if jenis == "pindah_pita":
            # FIX-PITA-TABEL-ATAS: tabel tetap di tempat; teks pita di atasnya dipindah ke bawah tabel
            peringatan.append("{} dinaikkan ke puncak hlm {}: pita teks di atasnya dipindahkan "
                              "ke bawah tabel".format(nama, halaman_awal + o[0] - 1))
        kol = not penuh.get(k, True)
        ko = kunci(o, kol)
        if n_sebelum:
            t = titik("pm-{}".format(n_sebelum))
            if t and not kunci(t, kol) < ko:
                # FIX-PITA-QA: sengaja dinaikkan ke atas halaman yang sama; FIX-TUNDA-ATAS: float tunda
                # yang mendarat di puncak halaman paragraf sebelumnya = aturan "atas halaman yang sama"
                if kp.get(k, {}).get("maju_hal"):  # FIX-ATAS-SAMA: atas halaman penandanya (urutan aturan ke-2)
                    peringatan.append("{} di atas hlm {} (halaman tempat ia disebut): tidak muat di posisinya".format(
                        nama, halaman_awal + o[0] - 1))
                elif kp.get(k, {}).get("maju"):   # FIX-OBJEK-JUDUL: sengaja naik agar tak melewati judul
                    peringatan.append("{} dinaikkan ke atas hlm {} agar tidak tampil sesudah judul bagian "
                                      "berikutnya".format(nama, halaman_awal + o[0] - 1))
                elif jenis == "atas" or (jenis == "tunda" and t[0] == o[0] and o[2] < 102.4 + 30):
                    peringatan.append("{} dinaikkan ke atas hlm {}: pita teks di atasnya kurang dari "
                                      "5 baris".format(nama, halaman_awal + o[0] - 1))
                elif jenis == "pindah_pita":
                    pass
                else:
                    masalah.append("{} muncul SEBELUM paragraf yang mendahuluinya di naskah (hlm {})".format(
                        nama, halaman_awal + o[0] - 1))
        if n_sesudah:
            t = titik("pm-{}".format(n_sesudah))
            if t and not kunci(t, kol) > ko:
                if jenis == "bawah":      # FIX-PITA-QA: sengaja diturunkan ke dasar halaman yang sama
                    peringatan.append("{} diturunkan ke dasar hlm {}: pita teks di bawahnya kurang dari "
                                      "5 baris".format(nama, halaman_awal + o[0] - 1))
                elif jenis == "geser" or (jenis == "pindah_pita" and kp.get(k, {}).get("target")):            # FIX-TABEL-GESER
                    peringatan.append("{} dimulai di hlm {} (tabel tinggi; paragraf sesudahnya mengisi ruang "
                                      "di halaman sebelumnya)".format(nama, halaman_awal + o[0] - 1))
                elif jenis == "atas":       # FIX-ATAS-SAMA: sengaja dinaikkan ke puncak halaman
                    peringatan.append("{} dinaikkan ke atas hlm {}: paragraf sesudahnya mengisi "
                                      "halaman sebelumnya".format(nama, halaman_awal + o[0] - 1))
                elif jenis == "tunda":
                    peringatan.append("{} ditunda ke atas hlm {}: tidak muat di posisinya dan perkecilan "
                                      "di bawah 70%; paragraf sesudahnya mengisi halaman sebelumnya".format(
                                          nama, halaman_awal + o[0] - 1))
                else:
                    masalah.append("{} muncul SESUDAH paragraf yang mengikutinya di naskah (hlm {})".format(
                        nama, halaman_awal + o[0] - 1))
        # FIX-OBJEK-JUDUL: objek tidak boleh tampil sebelum judul bagiannya sendiri
        h_sbl = judul_bagian_sebelum(body, n_sesudah or (n_sebelum + 1 if n_sebelum else None))
        if h_sbl is not None:
            t = titik("pm-{}".format(h_sbl))
            if t and not kunci(t, kol) < ko:
                m_j = re.search(r"^= #pm\({}\);(.*)$".format(h_sbl), body, flags=re.M)
                nm_j = m_j.group(1).strip() if m_j else ""
                if jenis in ("atas", "tunda") and t[0] == o[0]:
                    # di atas halaman tempat bagiannya dimulai: lazim di jurnal -> peringatan
                    peringatan.append("{} di atas hlm {}, sebelum judul bagiannya {} di halaman yang sama".format(
                        nama, halaman_awal + o[0] - 1, nm_j))
                else:
                    masalah.append("{} muncul SEBELUM judul bagiannya {} (hlm {})".format(
                        nama, nm_j, halaman_awal + o[0] - 1))
        # FIX-OBJEK-JUDUL: objek tidak boleh tampil sesudah judul bagian utama yang mengikutinya
        h_jdl = judul_bagian_sesudah(body, n_sesudah)
        if h_jdl is not None:
            t = titik("pm-{}".format(h_jdl))
            if t and not kunci(t, kol) > ko:
                m_j = re.search(r"^= #pm\({}\);(.*)$".format(h_jdl), body, flags=re.M)
                nm_j = m_j.group(1).strip() if m_j else "berikutnya"
                masalah.append("{} muncul SESUDAH judul bagian {} (hlm {})".format(
                    nama, nm_j, halaman_awal + o[0] - 1))
        if jenis == "kecil":
            peringatan.append("{} diperkecil ke {:.0%} agar tetap di posisi naskah".format(
                nama, kp[k].get("skala", 1)))
    for s in stat.get("kolom_kurang", []):
        gap = s["kurang"]
        if not kolom_sah(s, stat.get("jumlah_halaman")):
            masalah.append(pesan_kolom(s, stat.get("jumlah_halaman"), halaman_awal))
    # FIX-LUBANG-KOLOM: kolom yang berakhir > 2 baris di atas kolom lain (mis. float di puncak
    # kolom yang mendorong teks ke kolom sebelah)
    for lb in lubang_kolom(stat.get("pdf_qa", os.path.join(workdir, "_skala.pdf")), pos, penuh, body):
        sisi = "kiri" if lb["kol"] == 0 else "kanan"
        masalah.append("Lubang kolom {} hlm {} ({:.0f} pt lebih pendek dari kolom lain)".format(
            sisi, halaman_awal + lb["hal"] - 1, lb["selisih"]))
    if not cek_font:
        return masalah, peringatan, ringkas
    # font chart
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    rc, out, err = jalankan(["fc-match", "-f", "%{family}", font_cfg])
    harap = re.sub(r"[^a-z]", "", (out.split(",")[0] if rc == 0 and out else font_cfg).lower())
    for pdfc in sorted(glob.glob(os.path.join(workdir, "_chart", "chart*.pdf"))):
        salah = set()
        m_pdf = re.search(r"chart(\d+)\.pdf$", os.path.basename(pdfc))
        # FIX-QA-CHART-ASLI: PDF chart kini dirender pada ukuran font asli (mis. 9 pt),
        # lalu diperbesar saat ditampilkan. Ukuran tampil efektif diawasi CHART_EFEKTIF;
        # di sini cukup periksa jenis huruf dan jangan menuntut 11 pt di PDF mentah.
        ada_efektif = bool(m_pdf and ("chart_render_{}.png".format(m_pdf.group(1)) in CHART_EFEKTIF))
        with fitz.open(pdfc) as doc_chart:   # FIX-MEMORI-FITZ
            for blk in doc_chart[0].get_text("dict")["blocks"]:
                for l in blk.get("lines", []):
                    for sp in l["spans"]:
                        if not sp["text"].strip():
                            continue
                        nm = re.sub(r"[^a-z]", "", sp["font"].split("-")[0].lower())
                        if not nm.startswith(harap[:10]) or \
                                (not ada_efektif and abs(sp["size"] - 11) > 0.3):
                            salah.add("{} {:.1f}pt".format(sp["font"], sp["size"]))
        if salah:
            masalah.append("Teks chart {} tidak memakai {} 11 pt: {}".format(
                os.path.basename(pdfc), font_cfg, ", ".join(sorted(salah))))
    return masalah, peringatan, ringkas


VERBOSE = [False]          # FIX-VERBOSE: --verbose menampilkan setiap tahap & panggilan program
_HITUNG = {"typst": 0}


def cetak(pesan):
    """FIX-VERBOSE: tampilkan pesan proses (stderr) bila --verbose."""
    if VERBOSE[0]:
        import time
        sys.stderr.write("[{}] {}\n".format(time.strftime("%H:%M:%S"), pesan))
        sys.stderr.flush()


class LogCetak(list):
    """Daftar log yang sekaligus dicetak saat --verbose."""
    def append(self, x):
        cetak("   " + str(x))
        super().append(x)


def jalankan(cmd, **kw):
    # FIX-PROGRAM-HILANG: program tidak ada (mis. fc-match di Windows) -> kode 127, bukan crash
    import time
    t0 = time.time()
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    except OSError as e:
        return 127, "", "program tidak dapat dijalankan: {} ({})".format(cmd[0], e)
    if VERBOSE[0]:
        nama = os.path.basename(cmd[0])
        if "typst" in nama.lower():
            _HITUNG["typst"] += 1
            sasaran = os.path.basename(cmd[-1]) if cmd[1] == "compile" else "kueri posisi"
            cetak("   typst #{} {} -> {} ({:.1f} s){}".format(
                _HITUNG["typst"], cmd[1], sasaran, time.time() - t0,
                "" if r.returncode == 0 else "  GAGAL"))
        else:
            cetak("   {} ({:.1f} s)".format(nama, time.time() - t0))
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--jurnal", required=True)
    ap.add_argument("--volume", type=int, required=True)
    ap.add_argument("--nomor", type=int, required=True, choices=[1, 2, 3, 4])
    ap.add_argument("--tahun", type=int, required=True)
    ap.add_argument("--halaman-awal", type=int, required=True)
    ap.add_argument("--meta", help="JSON metadata dari OJS (menimpa hasil urai docx)")
    ap.add_argument("--keluar", help="folder hasil (default: kerja/<nama>)")
    ap.add_argument("--batas-waktu", type=int, default=WAKTU_KANDIDAT_DETIK,
                    help="batas waktu (detik) pemilihan kandidat tata letak; makin besar makin teliti "
                         "tetapi makin lama (bawaan {})".format(WAKTU_KANDIDAT_DETIK))
    ap.add_argument("--tanpa-normalisasi", action="store_true",
                    help="jangan rapikan struktur naskah dengan normalisasi.php")
    ap.add_argument("--verbose", action="store_true",
                    help="tampilkan setiap tahap, setiap panggilan Typst, dan keputusan tata letak")
    a = ap.parse_args()
    VERBOSE[0] = a.verbose
    BATAS_WAKTU[0] = max(30, a.batas_waktu)

    docx_layout = os.path.abspath(a.docx)
    nama = os.path.splitext(os.path.basename(a.docx))[0]
    nama_aman = re.sub(r"[^A-Za-z0-9_\-]+", "_", nama)
    HALAMAN_AWAL_CETAK[0] = a.halaman_awal
    POTONG_KHUSUS.clear()   # Titik putus dicari oleh pencarian ketat
    workdir = os.path.join(BASE, "kerja", nama_aman)
    if os.path.isdir(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    # FIX-SKALA-SIDECAR: skala objek per naskah dari berkas <docx>.skala (mis. "1:0.72,7:0.90").
    # Dipakai bila SKALA_PAKSA tidak diberikan lewat lingkungan; berguna untuk naskah yang
    # kombinasi ukuran gambar terbaiknya tidak ditemukan pencarian otomatis.
    if not os.environ.get("SKALA_PAKSA"):
        _skala_sidecar = os.path.splitext(os.path.abspath(a.docx))[0] + ".skala"
        if os.path.isfile(_skala_sidecar):
            try:
                with open(_skala_sidecar, encoding="utf-8") as _f:
                    os.environ["SKALA_PAKSA"] = _f.read().strip()
            except OSError:
                pass
    cfg_path = os.path.join(BASE, "jurnal", a.jurnal, "config.json")
    if not os.path.isfile(cfg_path):
        sys.exit("Config jurnal tidak ditemukan: " + cfg_path)

    laporan = {"masalah": [], "peringatan": []}

    if not a.tanpa_normalisasi:                                     # FIX-NORMALISASI
        cetak("0. Normalisasi struktur naskah Word (normalisasi.php)")
        a.docx = normalisasi_naskah(os.path.abspath(a.docx), workdir, laporan)
    # FIX-SIDIK-ISI: resep diidentifikasi dari naskah yang benar-benar ditata (hasil
    # normalisasi), sedangkan lokasi berkas resep tetap di samping unggahan asli.
    docx_sidik = os.path.abspath(a.docx)
    cetak("1. Word -> pandoc (AST), konversi chart & gambar EMF")
    # 1. docx → AST
    with open(cfg_path, encoding="utf-8") as f:                     # FIX-CHART-TNR
        font_cfg = (json.load(f).get("font") or ["Times New Roman"])[0]
    docx_crop = potong_gambar_word(os.path.abspath(a.docx), workdir, laporan)    # FIX-CROP-WORD
    docx_crop, _ = render_bentuk_docx(docx_crop, workdir, laporan)               # FIX-BENTUK-WORD
    GARIS_TABEL[:] = baca_garis_tabel(os.path.abspath(a.docx))                  # FIX-GARIS-TABEL
    docx_sumber, n_chart = render_chart_docx(docx_crop, workdir, laporan,
                                             font_chart=font_cfg)  # FIX-CHART
    rc, out, err = jalankan([PANDOC, docx_sumber, "-t", "json",
                             "--extract-media=media"], cwd=workdir)
    if rc != 0:
        sys.exit("pandoc gagal: " + err)
    ast = json.loads(out)
    ast = normalkan_tabel_html(ast)
    ast["blocks"] = buka_blok_pandoc(ast["blocks"])
    if err.strip():
        laporan["peringatan"].append("pandoc: " + err.strip()[:300])

    cetak("2. Metadata & klasifikasi isi (judul, gambar, tabel, referensi)")
    # 2. metadata + isi
    meta, awal = urai_depan(ast["blocks"])
    if a.meta:
        with open(a.meta, encoding="utf-8") as f:
            meta.update({k: v for k, v in json.load(f).items() if v})
    meta["_artikel_id"] = nama
    nama_penulis = [p["nama"] for p in meta["penulis"]]
    judul_kata = meta["judul"].split()
    meta.update({
        "judul_kalimat": sentence_case(meta["judul"]),
        "sitasi_penulis": apa_penulis(nama_penulis),
        "header_ganjil": header_penulis(nama_penulis) + ", " + " ".join(judul_kata[:7])
                         + ("..." if len(judul_kata) > 7 else ""),
        # FIX-EDISI-CFG: bulan terbit per jurnal (config "edisi", mis. {"1": "June", "2": "December"})
        "terbitan": {"volume": a.volume, "nomor": a.nomor, "edisi": edisi_jurnal(cfg_path, a.nomor),
                     "tahun": a.tahun, "halaman_awal": a.halaman_awal},
    })
    LEBAR_TEKS_WORD[0] = lebar_teks_word(os.path.abspath(a.docx))   # FIX-LEBAR-TABEL
    with open(cfg_path, encoding="utf-8") as f:
        di_tempat = json.load(f).get("penempatan_lebar", "mengambang") == "di-tempat"
    ubah_emf(workdir, laporan)                                       # FIX-EMF
    TANPA_INDENT.update(baca_indent_word(os.path.abspath(a.docx)))  # FIX-INDENT-LANJUTAN
    blok_isi, stat = ubah_isi(ast["blocks"][awal:], workdir, laporan, di_tempat)
    # FIX-QA-GAMBAR: setiap gambar di naskah (di luar tabel) harus menjadi satu #gambar
    n_gambar_naskah = sum(json.dumps(x).count('"t": "Image"')
                          for x in ast["blocks"][awal:] if x.get("t") != "Table")
    if n_gambar_naskah != stat.get("gambar_sumber", stat["gambar"]):
        laporan["masalah"].append("Jumlah gambar tidak cocok: {} di naskah, {} ditata".format(
            n_gambar_naskah, stat.get("gambar_sumber", stat["gambar"])))
    # FIX-QA-RUMUS: isi rumus (Math, sumber TeX seperti \\frac, \\nabla) tidak muncul sebagai kata
    # di PDF -> tidak ikut diperiksa cakupan teks
    def tanpa_rumus(x):
        if isinstance(x, list):
            return [tanpa_rumus(y) for y in x if not (isinstance(y, dict) and y.get("t") == "Math")]
        if isinstance(x, dict):
            return {k: tanpa_rumus(v) for k, v in x.items()}
        return x
    blok_qa = tanpa_rumus(ast["blocks"][awal:])
    teks_sumber = " ".join(stringify(inlines_of(b) or []) for b in blok_qa)  # FIX-NOMOR
    for b in blok_qa:
        if b.get("t") == "Table":
            teks_sumber += " " + json.dumps(b)    # kata dalam tabel ikut diperiksa
    teks_sumber = re.sub(r'"t":\s*"\w+"|"c":|\b(?:null|true|false)\b', " ", teks_sumber)   # FIX-QA-JSON
    # FIX-QA-JSON-UNICODE: escape “/” di JSON tabel membuat "cArtificial" (huruf c
    # dari escape menempel ke kata berikutnya). Buang semua escape unicode sebelum ekstraksi kata.
    teks_sumber = re.sub(r"\\u[0-9a-fA-F]{4}", " ", teks_sumber)
    # FIX-QA-SOFT-HYPHEN: samakan dengan teks PDF (soft hyphen literal maupun escape JSON).
    teks_sumber = teks_sumber.replace("\xad", "").replace("\\u00ad", "")

    with open(os.path.join(workdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    stat["resep_layout"] = muat_resep_layout(docx_layout, meta, docx_sidik)
    cetak("3. AST -> body.typ")
    # 3. AST → body.typ
    ast["blocks"] = blok_isi
    ast = tabel_ast_typst(ast, workdir)
    rc, out, err = jalankan([PANDOC, "-f", "json", "-t", "typst", "--wrap=none",
                             "-o", "body.typ"], input=json.dumps(ast).encode("utf-8"), cwd=workdir)
    if rc != 0:
        sys.exit("pandoc (typst) gagal: " + err)
    body_path = os.path.join(workdir, "body.typ")
    with open(body_path, encoding="utf-8") as f:
        body = f.read()
    # FIX-URL-SPASI: spasi rusak di dalam URL ("https://www. evangelizatio.va") dibuang
    # berulang sampai stabil; hanya setelah titik/garis miring di dalam URL.
    for _ in range(4):
        baru = re.sub(r'(https?://[^\s")\]]*[./])[ \t]+(?=[A-Za-z0-9])', r'\1', body)
        if baru == body:
            break
        body = baru
    # FIX-RUJUKAN-WORD: rujukan silang Word (#link(<_Ref...>)[Table 2]) menunjuk bookmark yang
    # tidak ada di Typst -> teksnya dipertahankan, tautannya dibuang
    body = re.sub(r"#link\(<_[A-Za-z]+\d+>\)\[([^\[\]]*)\]", r"\1", body)
    # FIX-TAUTAN-DOI: hyperlink rusak dari penulis ("http://doi:10.xxx") -> https://doi.org/10.xxx
    body = re.sub(r'#link\("https?://(?:dx\.)?doi:\s*(10\.[^"]+)"\)', r'#link("https://doi.org/\1")', body)
    body = re.sub(r'(media/[^"\n]+)\.(?:emf|wmf)"', r'\1.png"', body, flags=re.I)
    # Buang pembungkus #figure bawaan pandoc di sekitar tabel (keterangan diurus template)
    # FIX-TABEL-CAPTION: pandoc dapat menyertakan "caption: [...]" (keterangan Word yang dikenali
    # sebagai keterangan tabel); keterangan sudah diurus build.py, jadi ikut dibuang.
    pola_fig = re.compile(r"#figure\(\s*align\(center\)\[(#table\(.*?\n  \))\]\s*"
                          r"(?:,\s*caption: \[(.*?)\n\s*\]\s*)?,\s*kind: table\s*\)", flags=re.S)
    potong, n, akhir = [], 0, 0
    for m in pola_fig.finditer(body):
        depan = "".join(potong) + body[akhir:m.start()]
        ket = re.sub(r"^\s*(?:#strong\[)?(?:Table|Tabel)\s+\d+\]?[.:]?\s*", "", m.group(2) or "").strip()
        i = depan.rfind("#tabel(")
        if ket and i >= 0 and depan.find("isi: [])[", i) > 0:   # keterangan Word ditangkap pandoc
            j = depan.find("isi: [])[", i)
            depan = depan[:j] + "isi: [" + ket + "])[" + depan[j + len("isi: [])["):]
        potong, akhir, n = [depan + m.group(1)], m.end(), n + 1
    body = "".join(potong) + body[akhir:]
    body, tambahan_tabel = buka_figure_tabel(body)
    n += tambahan_tabel
    body = gaya_tabel_typst(body)
    # FIX-TABEL-PT: lebar kolom tabel (pt) menurut isinya, urut sesuai kemunculan tabel tingkat atas
    lebar_tb = list(stat.get("lebar_tabel", []))
    bag_tb, pos_tb = [], 0
    pola_tb = re.compile(r"#table\(\n\s*columns: (\([^)\n]*\)|\d+)")
    while True:
        m_tb = pola_tb.search(body, pos_tb)
        if not m_tb:
            break
        buka_tb = body.index("(", m_tb.start())
        akhir_tb = akhir_kurung_typst(body, buka_tb)
        bag_tb.append(body[pos_tb:m_tb.start(1)])
        lb = lebar_tb.pop(0) if lebar_tb else None
        kol_str = m_tb.group(1)
        n_kol_tb = int(kol_str) if kol_str.isdigit() else len([x for x in kol_str.strip("()").split(",") if x.strip()])
        if lb and len(lb) == n_kol_tb:
            bag_tb.append("(" + ", ".join("{:.1f}pt".format(w) for w in lb) + ")")
        else:
            bag_tb.append(kol_str)
        if akhir_tb is not None:
            pos_tb = akhir_tb + 1
            bag_tb.append(body[m_tb.end(1):pos_tb])
        else:
            pos_tb = m_tb.end(1)
    bag_tb.append(body[pos_tb:])
    body = "".join(bag_tb)
    if n != stat.get("tabel_pandoc", stat["tabel"]):
        laporan["masalah"].append("Pembungkus tabel pandoc: {} dari {} tertangani".format(
            n, stat.get("tabel_pandoc", stat["tabel"])))
    # FIX-ROWSPAN-PISAH: (di-tempat) sel gabungan (rowspan) boleh terpotong ANTAR baris di batas
    # halaman; tiap baris tetap utuh (FIX-BARIS-UTUH). Tanpa ini satu kelompok rowspan dianggap
    # satu blok sehingga tabel tidak dapat mulai di sisa halaman.
    if di_tempat:
        body = re.sub(r"(table\.cell\([^)]*?\browspan: \d+)", r"\1, breakable: true", body)
        # Perataan sel dan padding ditentukan template, bukan override Word/Pandoc.
        body = re.sub(r"^    align: \([^)\n]*\),\n", "", body, flags=re.M)
        body = re.sub(r"table\.cell\(([^)]*)\)", lambda m: "table.cell(" + ", ".join(
            a.strip() for a in m.group(1).split(",") if a.strip() and not a.strip().startswith("align:")) + ")", body)
    # FIX-KEPUTUSAN-LUAR: placeholder keputusan untuk tiap objek di-tempat (#gambar/#tabel)
    n_objek = [0]
    def _beri_nomor(m):
        n_objek[0] += 1
        return m.group(1) + 'keputusan: "@K{}@", '.format(n_objek[0])
    body = re.sub(r'(#(?:gambar|tabel)\([^\n]*?di-tempat: true, )', _beri_nomor, body)
    body_dasar = '#import "/template_jurnal.typ": *\n\n' + body
    # FIX-CELAH-GAMBAR-DAFTAR: celah sebelum float selebar halaman yang langsung diikuti daftar
    # dipindah ke sesudah float (bukan dihapus) agar jarak paragraf->daftar tetap ada.
    body_dasar = re.sub(
        r'#celah\(\)\n(#gambar\([^\n]*keputusan: (?:"@K\d+@"|"[^"]+")[^\n]*\))\n\n(#butir\()',
        r'\1\n\n#celah()\n\2', body_dasar)
    m_ref = re.search(r"#referensi\[#pm\((\d+)\);", body)      # FIX-REF-TUNGGAL3
    REF_PM_AWAL[0] = int(m_ref.group(1)) if m_ref else None
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(body_dasar)

    rel = os.path.relpath(workdir, BASE).replace(os.sep, "/")
    with open(os.path.join(workdir, "main.typ"), "w", encoding="utf-8") as f:
        f.write('#import "/template_jurnal.typ": *\n'
                '#let cfg = json("/jurnal/{j}/config.json")\n'
                '#let meta = json("/{r}/meta.json")\n'
                '#show: artikel.with(cfg, meta)\n'
                '#include "body.typ"\n'.format(j=a.jurnal, r=rel))

    cetak("4. Tata letak: posisi objek, looping ukuran gambar, perataan kolom")
    # 4. Typst → PDF (Loop Adaptif Gambar vs Kolom)
    keluar = a.keluar or workdir
    os.makedirs(keluar, exist_ok=True)
    pdf = os.path.join(keluar, nama_aman + ".pdf")
    keputusan = {k: {"jenis": "tempat"} for k in range(1, n_objek[0] + 1)}

    # FIX-OPTIMASI-SKALA: (1) tempatkan objek, (2) looping ukuran gambar untuk seluruh dokumen,
    # (3) periksa ulang penempatan (perkecilan dapat menggeser objek lain), (4) geser titik bagi
    # kolom per baris. Tidak ada peregangan spasi kata/antarparagraf: baris kiri-kanan sejajar.
    log_k, kandidat = LogCetak(), []
    if keputusan:
        keputusan, log_p = putuskan_objek(body_dasar, body_path, workdir, pdf, keputusan,
                                          stat.get("peta_objek", []))
        log_k += log_p
    if di_tempat:
        # FIX-SKALA-PAKSA: override skala objek dari lingkungan (mis. "1:0.72") untuk
        # menguji kombinasi ukuran yang tidak dihasilkan pencarian otomatis.
        paksa = os.environ.get("SKALA_PAKSA", "")
        for bag in paksa.split(","):
            if ":" in bag:
                kk, ss = bag.split(":", 1)
                try:
                    keputusan[int(kk)]["skala"] = float(ss)
                except (KeyError, ValueError):
                    pass
        keputusan = pilih_kandidat(body_dasar, body_path, workdir, keputusan, kandidat, laporan, log_k, stat)
    stat["body_dasar"] = body_dasar
    # Semua halaman memakai gerbang yang sama; hasil terpilih tidak diubah pass hardcode.
    laporan["log_objek"] = log_k + laporan.get("log_objek", [])
    stat["keputusan"] = keputusan
    cetak("5. Kompilasi akhir & pemeriksaan QA")
    # FIX-KONVERGENSI-HALAMAN-AKHIR: rentang halaman di kepala halaman 1 tidak lagi memakai
    # counter(page).final() (introspection final yang berayun saat float mengubah pagination).
    # Kompilasi sekali, baca jumlah halaman, tulis ke meta, lalu kompilasi ulang. Perubahan
    # teks kepala halaman bersifat place() sehingga tidak mengubah tata letak.
    def _tulis_meta():
        with open(os.path.join(workdir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    for _ in range(2):
        rc, out, err = jalankan([TYPST, "compile", "--root", BASE,
                                 "--font-path", os.path.join(BASE, "fonts"),
                                 os.path.join(workdir, "main.typ"), pdf])
        if rc != 0:
            break
        akhir = a.halaman_awal + _jumlah_halaman(pdf) - 1
        if meta.get("_halaman_akhir") == akhir:
            break
        meta["_halaman_akhir"] = akhir
        _tulis_meta()
    peringatan_typst = [l for l in err.splitlines() if l.startswith(("warning", "error"))]
    if rc != 0:
        hasil = {"status": "GAGAL", "masalah": ["Typst gagal mengompilasi"], "log": err[-3000:]}
    else:
        sesuaikan_chart_skala(os.path.join(workdir, "body.typ"))
        hasil = qa(pdf, meta, stat, laporan, teks_sumber, peringatan_typst)
        hasil["pdf"] = pdf
        hasil["keputusan"] = keputusan
        hasil["layout_terpilih"] = stat.get("layout_terpilih", {})
        hasil["perbaikan_target"] = laporan.get("perbaikan_target", [])
        hasil["perbaikan_referensi"] = laporan.get("perbaikan_referensi", {})
        if di_tempat:   # FIX-QA-TATA-LETAK
            pos_akhir, _ = kueri_posisi(os.path.join(workdir, "main.typ"))
            stat["body_dasar"] = body_dasar
            stat["jumlah_halaman"] = _jumlah_halaman(pdf)
            # FIX-QA-KOLOM-FINAL: kolom_kurang dari pemilihan kandidat bisa basi karena pass
            # grid/urutan/widow mengubah body sesudahnya; ukur ulang dari PDF final.
            try:
                with open(os.path.join(workdir, "body.typ"), encoding="utf-8") as f:
                    stat["kolom_kurang"] = ukur_kolom(pdf, pos_akhir or {}, f.read())
            except (OSError, ValueError) as e:
                hasil["masalah"].append("Pengukuran kolom gagal: " + str(e))
            stat["pdf_qa"] = pdf
            m2, p2, r2 = qa_tata_letak(stat, pos_akhir or {}, workdir, a.halaman_awal, font_cfg)
            # FIX-PITA-QA: pita teks 1-4 baris di atas/bawah/di antara objek -> ditahan
            penuh_akhir = {k: objek_penuh(body_dasar[slice(*blok_objek(body_dasar, k))])
                           for (k, _, _) in stat.get("peta_objek", [])}
            for pt in pita_kecil(pdf, pos_akhir or {}, penuh_akhir):
                if pt["letak"] == "total":
                    m2.append("Halaman {} hanya berisi {} baris teks per kolom di sekitar objek "
                              "(min. {})".format(a.halaman_awal + pt["hal"] - 1, pt["baris"], PITA_TOTAL_MIN))
                    continue
                batas_pita = PITA_TENGAH_MIN if pt["letak"] == "tengah" else PITA_TEPI_MIN
                m2.append("Pita teks hanya {} baris ({} objek, min. {}) di hlm {}".format(
                    pt["baris"], {"atas": "di atas", "bawah": "di bawah", "tengah": "di antara"}[pt["letak"]],
                    batas_pita, a.halaman_awal + pt["hal"] - 1))
            with open(body_path, encoding="utf-8") as f:
                teks_final = f.read()
            try:
                alignment = ukur_layout_ketat(pdf, pos_akhir or {}, teks_final)
            except (ValueError, KeyError) as e:
                alignment = dict(lulus=False, masalah=["Pengukuran final gagal: " + str(e)],
                                 kolom=[])
            m2 += alignment["masalah"]
            hasil["alignment"] = alignment
            hasil["alignment_pencarian"] = stat.get("alignment_pencarian", {})
            hasil["masalah"] = list(dict.fromkeys(hasil["masalah"]))
            hasil["masalah"] = list(dict.fromkeys(hasil["masalah"] + m2))
            hasil["peringatan"] += p2
            hasil["objek"] = r2
            hasil["log_objek"] = laporan.get("log_objek", [])   # FIX-LOG-KEPUTUSAN
            hasil["status"] = "DITAHAN" if hasil["masalah"] else hasil["status"]
    if hasil["status"] == "LULUS" and di_tempat:
        layout = stat["layout_terpilih"]
        try:
            simpan_resep_layout(docx_layout, meta, keputusan,
                                layout["header_grid"], layout["potongan"],
                                layout.get("akhir_seimbang", False), docx_sidik)
            hasil["resep_layout_disimpan"] = True
        except OSError as e:
            hasil["peringatan"].append("Resep layout tidak dapat disimpan: " + str(e))
    with open(os.path.join(keluar, nama_aman + ".qa.json"), "w", encoding="utf-8") as f:
        json.dump(hasil, f, ensure_ascii=False, indent=2)
    # FIX-KONSOL-UNICODE: stdout Windows sering cp1252; teks naskah dapat memuat karakter
    # non-cp1252 (mis. bullet ▪ pada catatan normalisasi). Jangan gagalkan QA hanya karena
    # pencetakan konsol -- escape karakter yang tidak terwakili.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    print(json.dumps(hasil, ensure_ascii=False, indent=2))
    sys.exit(0 if hasil["status"] == "LULUS" else 2)


if __name__ == "__main__":
    main()
