#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pengujian unit dan integrasi untuk paket QA PDF mandiri (/opt/typst_editor/qa_standalone)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

STANDALONE_DIR = Path(__file__).resolve().parent
if str(STANDALONE_DIR) not in sys.path:
    sys.path.insert(0, str(STANDALONE_DIR))

from qa_engine import analyze_pdf, export_html_report, render_annotated_page  # noqa: E402
from rules import JournalRules  # noqa: E402


def _add_col_lines(
    page: fitz.Page,
    x: float,
    y_start: float,
    count: int,
    prefix: str = "Sample col text",
    fontname: str = "tiro",
    fontsize: float = 11.0,
    step: float = 11.0,
    indent_first: float = 0.0,
) -> float:
    y = y_start
    for idx in range(count):
        lx = x + (indent_first if idx == 0 else 0.0)
        page.insert_text((lx, y), f"{prefix} #{idx + 1} line filled nicely.", fontname=fontname, fontsize=fontsize)
        y += step
    return y - step


class TestQAStandalone(unittest.TestCase):
    def setUp(self):
        self.rules = JournalRules()
        self.tmp = tempfile.TemporaryDirectory(prefix="qa_standalone_test_")
        self.tmp_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_two_column_pdf_passes(self):
        pdf_path = self.tmp_dir / "clean.pdf"
        doc = fitz.open()
        # Halaman 1: blok judul + kotak abstrak lalu 2 kolom penuh
        p1 = doc.new_page(width=595.28, height=841.89)
        p1.insert_text((76.535, 110.0), "Sample Journal Article Title Spanning Columns", fontname="tibo", fontsize=13)
        p1.insert_text((76.535, 145.0), "To cite this article: Author (2026).", fontname="tiro", fontsize=8)
        p1.draw_line((76.535, 154.0), (518.735, 154.0), color=(0, 0, 0), width=1.0)
        _add_col_lines(p1, 76.535, 177.2, 53, prefix="Intro column text")
        _add_col_lines(p1, 305.135, 177.2, 53, prefix="Right column text")

        # Halaman 2 (halaman akhir): heading + >=3 baris + gambar 1 kolom sah
        p2 = doc.new_page(width=595.28, height=841.89)
        p2.insert_text((76.535, 111.2), "2. Research Findings", fontname="tibo", fontsize=11)
        _add_col_lines(p2, 76.535, 122.2, 6, prefix="Findings line")
        p2.draw_rect(fitz.Rect(76.535, 200.0, 290.0, 310.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        p2.insert_text((76.535, 322.0), "Figure 1. Valid one-column chart", fontname="tiro", fontsize=10)
        _add_col_lines(p2, 305.135, 111.2, 6, prefix="Closing text")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        self.assertEqual(report.status, "LULUS", [i.to_dict() for i in report.issues])
        self.assertEqual(report.fatal_count + report.error_count, 0)

    def test_right_column_longer_than_left_detected(self):
        pdf_path = self.tmp_dir / "right_longer.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 20, prefix="Left column shorter text")
        _add_col_lines(p1, 305.135, 111.2, 24, prefix="Right column longer text")
        p2 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p2, 76.535, 111.2, 10, prefix="Final page left")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        self.assertEqual(report.status, "PERLU PERBAIKAN")
        self.assertTrue(
            any(i.category == "kolom" and i.severity == "fatal" and "kanan lebih panjang" in i.title.lower()
                for i in report.issues),
            [i.to_dict() for i in report.issues],
        )

    def test_hanging_left_column_detected(self):
        pdf_path = self.tmp_dir / "left_hang.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 48, prefix="Left column stops early")
        _add_col_lines(p1, 305.135, 111.2, 48, prefix="Right column also stops early")
        p2 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p2, 76.535, 111.2, 58, prefix="Page 2 full baseline anchor")
        _add_col_lines(p2, 305.135, 111.2, 58, prefix="Page 2 right anchor")
        p3 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p3, 76.535, 111.2, 10, prefix="Closing lines")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        self.assertEqual(report.status, "PERLU PERBAIKAN")
        self.assertTrue(
            any(i.page == 1 and i.category == "kolom" and i.column == 0 and "menggantung" in i.title.lower()
                for i in report.issues),
            [i.to_dict() for i in report.issues],
        )

    def test_orphan_heading_zero_and_one_line_detected(self):
        pdf_path = self.tmp_dir / "orphan_heading.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 57, prefix="Body filling col 0")
        p1.insert_text((76.535, 749.2), "3. Orphan Heading Alone", fontname="tibo", fontsize=11)
        _add_col_lines(p1, 305.135, 111.2, 56, prefix="Body filling col 1")
        p1.insert_text((305.135, 738.2), "4. Heading With One Line", fontname="tibo", fontsize=11)
        p1.insert_text((305.135, 749.2), "Single body line right below heading.", fontname="tiro", fontsize=11)

        # Tambah halaman ke-2 dengan heading + 2 baris untuk menguji opsi min_heading_lines=3
        p2 = doc.new_page(width=595.28, height=841.89)
        p2.insert_text((76.535, 111.2), "5. Heading With Two Lines", fontname="tibo", fontsize=11)
        _add_col_lines(p2, 76.535, 122.2, 2, prefix="Two lines under heading")
        _add_col_lines(p2, 305.135, 111.2, 4, prefix="Right closing text")
        doc.save(str(pdf_path))
        doc.close()

        rep2 = analyze_pdf(str(pdf_path), rules=JournalRules(min_heading_lines=2))
        hdg_p1 = [i for i in rep2.issues if i.page == 1 and i.category == "orphan_heading"]
        self.assertEqual(len(hdg_p1), 2, [i.to_dict() for i in rep2.issues])
        self.assertFalse(any(i.page == 2 and i.category == "orphan_heading" for i in rep2.issues))

        rep3 = analyze_pdf(str(pdf_path), rules=JournalRules(min_heading_lines=3))
        self.assertTrue(any(i.page == 2 and i.category == "orphan_heading" for i in rep3.issues))

    def test_widow_and_orphan_lines_detected(self):
        # Kolom kiri penuh, baris terakhir berindentasi & mencapai tepi kanan -> ORPHAN;
        # Kolom kanan dibuka 1 baris lanjutan pendek lalu spasi paragraf -> WIDOW.
        pdf_explicit = self.tmp_dir / "widow_orphan_exact.pdf"
        doc2 = fitz.open()
        pa = doc2.new_page(width=595.28, height=841.89)
        for i in range(58):
            y = 111.2 + i * 11.0
            if i == 57:
                pa.insert_text((90.5, y), "Opening sentence of new paragraph right.", fontname="tiro", fontsize=11)
            else:
                pa.insert_text((76.535, y), f"Filler line #{i:02d} filling full column width.", fontname="tiro", fontsize=11)
        pa.insert_text((305.135, 111.2), "end word.", fontname="tiro", fontsize=11)
        pa.insert_text((319.1, 133.2), "New paragraph starting cleanly below it.", fontname="tiro", fontsize=11)
        for i in range(2, 58):
            pa.insert_text((305.135, 111.2 + i * 11.0), f"Right col line #{i:02d} filling full column.", fontname="tiro", fontsize=11)
        doc2.save(str(pdf_explicit))
        doc2.close()

        report = analyze_pdf(str(pdf_explicit), rules=self.rules)
        cats = {i.category for i in report.issues}
        self.assertIn("orphan", cats, [i.to_dict() for i in report.issues])
        self.assertIn("widow", cats, [i.to_dict() for i in report.issues])

    def test_object_margin_and_gutter_violations_detected(self):
        pdf_path = self.tmp_dir / "obj_violations.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        # 1. Objek menabrak margin kiri (< 76.5) dan gutter tengah (> 290.1)
        p1.draw_rect(fitz.Rect(60.0, 120.0, 300.0, 220.0), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
        # 2. Objek lebar penuh berpusat rapi (TIDAK boleh memicu pelanggaran gutter)
        p1.draw_rect(fitz.Rect(80.0, 260.0, 515.0, 360.0), color=(0, 0, 0), fill=(0.85, 0.85, 0.85))
        # 3. Objek keluar margin kanan (> 518.7) dan bawah (> 762.9)
        p1.draw_rect(fitz.Rect(306.0, 680.0, 540.0, 780.0), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
        _add_col_lines(p1, 76.535, 380.0, 10, prefix="Text below full-width object")
        _add_col_lines(p1, 305.135, 380.0, 10, prefix="Text in right col")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        obj_issues = [i for i in report.issues if i.category == "margin_objek"]
        titles = " | ".join(i.title.lower() for i in obj_issues)
        self.assertIn("margin kiri", titles)
        self.assertIn("gutter", titles)
        self.assertIn("margin kanan", titles)
        self.assertIn("batas bawah", titles)
        # Pastikan kotak full-width (y=260..360) tidak diflag sebagai pelanggaran objek
        self.assertFalse(
            any(i.bbox and abs(i.bbox[1] - 260.0) < 2.0 for i in obj_issues),
            [i.to_dict() for i in obj_issues],
        )

    def test_render_annotated_page_and_html_export_and_cli(self):
        pdf_path = self.tmp_dir / "sample_cli.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 10, prefix="Left lines")
        _add_col_lines(p1, 305.135, 111.2, 14, prefix="Right longer lines")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        img = render_annotated_page(str(pdf_path), 1, report.issues, rules=self.rules, dpi=96, highlight_issue_idx=0)
        self.assertGreater(img.width, 400)
        self.assertGreater(img.height, 600)

        html_out = self.tmp_dir / "report.html"
        export_html_report(report, str(html_out))
        html_txt = html_out.read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,", html_txt)
        self.assertIn("PERLU PERBAIKAN", html_txt)

        json_out = self.tmp_dir / "report.json"
        proc = subprocess.run(
            [sys.executable, str(STANDALONE_DIR / "qa_cli.py"), str(pdf_path), "--json", str(json_out)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        data = json.loads(json_out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "PERLU PERBAIKAN")
        self.assertGreater(data["counts"]["total"], 0)

        # Pastikan app_gui dapat diimpor bersih
        import app_gui  # noqa: F401
        self.assertTrue(hasattr(app_gui, "QALayoutApp"))

    def test_text_optical_overhang_allowed_but_object_strict(self):
        pdf_path = self.tmp_dir / "optical_overhang.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        # Teks rata kanan-kiri menjulur optik sedikit (col 0 ~294.5 pt, col 1 ~522.5 pt)
        for i in range(6):
            y = 111.2 + i * 11.0
            p1.insert_text((76.535, y), "Optical overhang justified line reaches 294.", fontname="tiro", fontsize=11)
            p1.insert_text((305.135, y), "Right column optical overhang reaches 523.", fontname="tiro", fontsize=11)
        # Objek gambar 1 kolom melanggar gutter (x1=295.1) & gambar menabrak margin kanan (x1=523.7)
        p1.draw_rect(fitz.Rect(76.535, 220.0, 295.1, 300.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        p1.draw_rect(fitz.Rect(305.135, 220.0, 523.7, 300.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        self.assertFalse(any(i.category == "margin_teks" for i in report.issues), [i.to_dict() for i in report.issues])
        obj_issues = [i for i in report.issues if i.category == "margin_objek"]
        titles = " | ".join(i.title.lower() for i in obj_issues)
        self.assertIn("gutter", titles)
        self.assertIn("margin kanan", titles)

    def test_running_footer_ignored_by_grid_and_margin(self):
        pdf_path = self.tmp_dir / "running_footer.pdf"
        doc = fitz.open()
        for pg_no in (1, 2):
            pg = doc.new_page(width=595.28, height=841.89)
            _add_col_lines(pg, 76.535, 111.2, 60 if pg_no == 1 else 6, prefix="Clean text")
            _add_col_lines(pg, 305.135, 111.2, 60 if pg_no == 1 else 6, prefix="Right text")
            if pg_no == 2:
                pg.draw_line((76.535, 765.5), (518.735, 765.5), color=(0.5, 0.5, 0.5), width=0.8)
                pg.insert_text((76.535, 774.6), "656 | Bulletin of Social Studies and Community Development", fontname="tiro", fontsize=10)
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        self.assertEqual(report.status, "LULUS", [i.to_dict() for i in report.issues])
        self.assertEqual(report.warning_count, 0, [i.to_dict() for i in report.issues])

    def test_minor_baseline_jitter_not_false_positive(self):
        pdf_ok = self.tmp_dir / "jitter_ok.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 10, prefix="Left col line")
        _add_col_lines(p1, 305.135, 114.0, 10, prefix="Right col line")  # selisih 2.8 pt (< 4.5 pt)
        doc.save(str(pdf_ok))
        doc.close()

        rep_ok = analyze_pdf(str(pdf_ok), rules=JournalRules(check_grid=False))
        self.assertFalse(any(i.category == "kolom" for i in rep_ok.issues), [i.to_dict() for i in rep_ok.issues])

    def test_equation_format_rules(self):
        pdf_bad = self.tmp_dir / "eq_bad.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        p1.insert_text((76.535, 111.2), "Formula used in this calculation is:", fontname="tiro", fontsize=11)
        # Rumus SALAH: tegak (tiro), indentasi (+15 pt), tanpa spasi 11 pt di atas & bawah
        p1.insert_text((91.535, 122.2), "AAI = DPPH (ug/mL) / IC50 (ug/mL)", fontname="tiro", fontsize=11)
        p1.insert_text((76.535, 133.2), "where IC50 is the inhibition concentration.", fontname="tiro", fontsize=11)
        _add_col_lines(p1, 305.135, 111.2, 4, prefix="Right col lines")
        doc.save(str(pdf_bad))
        doc.close()

        rep_bad = analyze_pdf(str(pdf_bad), rules=self.rules)
        eq_msgs = " | ".join(i.title.lower() for i in rep_bad.issues if i.category == "rumus")
        self.assertIn("miring", eq_msgs)
        self.assertIn("menjorok", eq_msgs)
        self.assertIn("atas rumus", eq_msgs)
        self.assertIn("bawah rumus", eq_msgs)

        pdf_good = self.tmp_dir / "eq_good.pdf"
        doc2 = fitz.open()
        p2 = doc2.new_page(width=595.28, height=841.89)
        p2.insert_text((76.535, 111.2), "Formula used in this calculation is:", fontname="tiro", fontsize=11)
        # Rumus BENAR: italic (tiit), rata kiri (76.535), spasi >= 11 pt di atas & bawah
        p2.insert_text((76.535, 133.2), "AAI = DPPH (ug/mL) / IC50 (ug/mL)", fontname="tiit", fontsize=11)
        p2.insert_text((76.535, 155.2), "where IC50 is the inhibition concentration.", fontname="tiro", fontsize=11)
        _add_col_lines(p2, 305.135, 111.2, 5, prefix="Right col lines")
        doc2.save(str(pdf_good))
        doc2.close()

        rep_good = analyze_pdf(str(pdf_good), rules=self.rules)
        self.assertFalse(any(i.category == "rumus" for i in rep_good.issues), [i.to_dict() for i in rep_good.issues])

    def test_hanging_left_column_four_lines_and_blank_below_image(self):
        pdf_path = self.tmp_dir / "blank_below_img.pdf"
        doc = fitz.open()
        # Halaman 1 (non-akhir): kiri menggantung 5 baris, kanan ada gambar + ruang kosong besar di bawahnya
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 54, prefix="Left stops 5 lines early")
        _add_col_lines(p1, 305.135, 111.2, 10, prefix="Right text above figure")
        p1.draw_rect(fitz.Rect(305.135, 240.0, 518.0, 380.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        p2 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p2, 76.535, 111.2, 59, prefix="Anchor full page two")
        _add_col_lines(p2, 305.135, 111.2, 59, prefix="Anchor full right")
        p3 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p3, 76.535, 111.2, 8, prefix="Closing")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        col_titles = " | ".join(i.title.lower() for i in report.issues if i.page == 1 and i.category == "kolom")
        self.assertIn("kolom kiri menggantung", col_titles)
        self.assertIn("ruang kosong besar di bawah objek", col_titles)

    def test_three_consecutive_objects_and_empty_column_detected(self):
        pdf_path = self.tmp_dir / "consecutive_objs.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        # 3 objek berturut-turut di kolom kiri tanpa teks, dan kolom kanan kosong melompong
        p1.draw_rect(fitz.Rect(76.535, 110.0, 289.0, 210.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        p1.draw_rect(fitz.Rect(76.535, 225.0, 289.0, 325.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        p1.draw_rect(fitz.Rect(76.535, 340.0, 289.0, 440.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        p2 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p2, 76.535, 111.2, 10, prefix="Closing text")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        self.assertTrue(any(i.category == "tata_letak_objek" for i in report.issues), [i.to_dict() for i in report.issues])
        self.assertTrue(
            any(i.category == "kolom" and "kosong melompong" in i.title.lower() for i in report.issues),
            [i.to_dict() for i in report.issues],
        )

    def test_citation_to_intro_gap_22pt(self):
        # Kasus 1: jarak hanya 8 pt (< 22 pt) -> terdeteksi jarak_sitasi
        pdf_bad = self.tmp_dir / "cite_bad.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        p1.insert_text((76.535, 150.0), "To cite this article: Author (2026).", fontname="tiro", fontsize=8)
        p1.draw_line((76.535, 158.0), (518.735, 158.0), color=(0, 0, 0), width=1.0)
        p1.insert_text((76.535, 174.5), "■ INTRODUCTION", fontname="tibo", fontsize=11)  # y0 ~ 166 -> gap ~ 8 pt
        _add_col_lines(p1, 76.535, 185.5, 4, prefix="Intro body")
        _add_col_lines(p1, 305.135, 174.5, 5, prefix="Right body")
        doc.save(str(pdf_bad))
        doc.close()

        rep_bad = analyze_pdf(str(pdf_bad), rules=self.rules)
        self.assertTrue(any(i.category == "jarak_sitasi" for i in rep_bad.issues), [i.to_dict() for i in rep_bad.issues])

        # Kasus 2: jarak 22 pt -> lolos tanpa jarak_sitasi
        pdf_ok = self.tmp_dir / "cite_ok.pdf"
        doc2 = fitz.open()
        p2 = doc2.new_page(width=595.28, height=841.89)
        p2.insert_text((76.535, 150.0), "To cite this article: Author (2026).", fontname="tiro", fontsize=8)
        p2.draw_line((76.535, 158.0), (518.735, 158.0), color=(0, 0, 0), width=1.0)
        p2.insert_text((76.535, 188.4), "■ INTRODUCTION", fontname="tibo", fontsize=11)  # y0 ~ 180.0 -> gap ~ 22.0 pt
        _add_col_lines(p2, 76.535, 199.4, 4, prefix="Intro body")
        _add_col_lines(p2, 305.135, 188.4, 5, prefix="Right body")
        doc2.save(str(pdf_ok))
        doc2.close()

        rep_ok = analyze_pdf(str(pdf_ok), rules=self.rules)
        self.assertFalse(any(i.category == "jarak_sitasi" for i in rep_ok.issues), [i.to_dict() for i in rep_ok.issues])

    def test_trapped_ribbon_between_two_objects_no_crash(self):
        pdf_path = self.tmp_dir / "trapped_ribbon.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        p1.draw_rect(fitz.Rect(80.0, 120.0, 515.0, 240.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        _add_col_lines(p1, 76.535, 265.0, 2, prefix="Trapped two lines")
        _add_col_lines(p1, 305.135, 265.0, 2, prefix="Trapped right")
        p1.draw_rect(fitz.Rect(80.0, 295.0, 515.0, 420.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        _add_col_lines(p1, 76.535, 445.0, 10, prefix="Text below")
        _add_col_lines(p1, 305.135, 445.0, 10, prefix="Right below")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        self.assertTrue(any(i.category == "pita_teks" for i in report.issues), [i.to_dict() for i in report.issues])

    def test_bottom_table_inner_rule_not_mistaken_for_footer_rule(self):
        pdf_path = self.tmp_dir / "bottom_table_footer.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 48, prefix="Body left text")
        _add_col_lines(p1, 305.135, 111.2, 48, prefix="Body right text")
        # Tabel di dasar halaman (y=668..748) dengan garis internal lebar > 65% text_width
        p1.draw_rect(fitz.Rect(153.5, 668.0, 441.7, 748.0), color=(0, 0, 0), width=0.7)
        p1.draw_line((153.5, 732.4), (441.7, 732.4), color=(0, 0, 0), width=0.7)
        p1.insert_text((170.0, 690.0), "Col A", fontname="tibo", fontsize=10)
        p1.insert_text((310.0, 690.0), "Col B", fontname="tibo", fontsize=10)
        # Running footer asli di y=765.5 (x=76.535..518.735)
        p1.draw_line((76.535, 765.5), (518.735, 765.5), color=(0, 0, 0), width=0.8)
        p1.insert_text((76.535, 774.6), "Bulletin of Social Studies | 701", fontname="tiro", fontsize=10)
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        footer_errs = [i for i in report.issues if i.category == "margin_objek" and "batas bawah" in i.title.lower()]
        self.assertEqual(footer_errs, [], [i.to_dict() for i in footer_errs])

    def test_symmetric_centered_two_column_table_not_flagged_as_gutter_violation(self):
        pdf_path = self.tmp_dir / "centered_table.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 8, prefix="Top left")
        _add_col_lines(p1, 305.135, 111.2, 8, prefix="Top right")
        # Tabel penuh terpusat simetris (x=170.6..423.7, lebar 253.1 pt) tanpa teks samping
        p1.draw_rect(fitz.Rect(170.6, 220.0, 423.7, 320.0), color=(0, 0, 0), width=0.8)
        p1.draw_line((170.6, 245.0), (423.7, 245.0), color=(0, 0, 0), width=0.8)
        # Objek melenceng dari pusat (x=110.0..340.0, lebar 230.0 pt) -> wajib tetap terdeteksi
        p1.draw_rect(fitz.Rect(110.0, 360.0, 340.0, 440.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        gutter_errs = [i for i in report.issues if i.category == "margin_objek" and "gutter" in i.title.lower()]
        self.assertEqual(len(gutter_errs), 1, [i.to_dict() for i in gutter_errs])
        self.assertAlmostEqual(gutter_errs[0].bbox[1], 360.0, delta=2.0)

    def test_complete_one_line_section_followed_by_heading_not_orphan_heading(self):
        pdf_path = self.tmp_dir / "one_line_section.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        p1.insert_text((76.535, 111.2), "■ CONFLICT OF INTEREST", fontname="tibo", fontsize=11)
        p1.insert_text((76.535, 122.2), "The authors declare no conflict of interest.", fontname="tiro", fontsize=11)
        p1.insert_text((76.535, 144.2), "■ ETHICS STATEMENT", fontname="tibo", fontsize=11)
        _add_col_lines(p1, 76.535, 155.2, 4, prefix="Ethics approval detail")
        _add_col_lines(p1, 305.135, 111.2, 8, prefix="Right col text")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        orphan_hdgs = [i for i in report.issues if i.category == "orphan_heading"]
        self.assertEqual(orphan_hdgs, [], [i.to_dict() for i in orphan_hdgs])

    def test_complete_one_line_declaration_section_at_column_bottom_not_orphan_heading(self):
        pdf_path = self.tmp_dir / "one_line_decl_bottom.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        _add_col_lines(p1, 76.535, 111.2, 59, prefix="Filled left column")
        _add_col_lines(p1, 305.135, 111.2, 57, prefix="Filled right column")
        p1.insert_text((305.135, 749.2), "■ CONFLICT OF INTEREST", fontname="tibo", fontsize=11)
        p1.insert_text((305.135, 760.2), "The authors declare no conflict of interest.", fontname="tiro", fontsize=11)
        p2 = doc.new_page(width=595.28, height=841.89)
        p2.insert_text((76.535, 111.2), "■ ETHICS STATEMENT", fontname="tibo", fontsize=11)
        _add_col_lines(p2, 76.535, 122.2, 4, prefix="Ethics approval text")
        _add_col_lines(p2, 305.135, 111.2, 5, prefix="Right col closing")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        orphan_hdgs = [i for i in report.issues if i.category == "orphan_heading"]
        self.assertEqual(orphan_hdgs, [], [i.to_dict() for i in orphan_hdgs])

    def _build_typography_fixture(
        self,
        path: Path,
        *,
        para_font: str = "tiro",
        para_size: float = 11.0,
        table_font: str = "tiro",
        table_size: float = 10.0,
        tbl_cap_font: str = "tibo",
        tbl_cap_size: float = 10.0,
        fig_cap_font: str = "tiro",
        fig_cap_size: float = 10.0,
    ) -> None:
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        p1.insert_text((76.535, 111.2), "■ 1. INTRODUCTION", fontname="tibo", fontsize=11)
        _add_col_lines(p1, 76.535, 122.2, 6, prefix="Body narrative", fontname=para_font, fontsize=para_size)
        # Superscript inline (7 pt) dan Note. (9 pt) harus dikecualikan tanpa false positive
        p1.insert_text((76.535, 188.2), "Value R", fontname="tiro", fontsize=11)
        p1.insert_text((112.0, 184.5), "2", fontname="tiro", fontsize=7.0)
        p1.insert_text((117.0, 188.2), " equals 0.95 in this model.", fontname="tiro", fontsize=11)
        _add_col_lines(p1, 305.135, 111.2, 7, prefix="Right narrative", fontname=para_font, fontsize=para_size)

        # Tabel dengan caption (2 baris uji multi-line) + kotak tabel
        p1.insert_text((76.535, 224.0), "Table 1.", fontname=tbl_cap_font, fontsize=tbl_cap_size)
        p1.insert_text((116.0, 224.0), "Summary of experimental results", fontname="tiro" if tbl_cap_font == "tibo" else tbl_cap_font, fontsize=tbl_cap_size)
        p1.insert_text((76.535, 235.0), "measured across classroom groups.", fontname="tiro" if tbl_cap_font == "tibo" else tbl_cap_font, fontsize=tbl_cap_size)
        p1.draw_rect(fitz.Rect(76.535, 242.0, 289.0, 315.0), color=(0, 0, 0), width=0.8)
        p1.draw_line((76.535, 265.0), (289.0, 265.0), color=(0, 0, 0), width=0.8)
        p1.draw_line((182.0, 242.0), (182.0, 315.0), color=(0, 0, 0), width=0.8)
        p1.insert_text((82.0, 257.0), "Metric", fontname="tibo" if table_font == "tiro" else table_font, fontsize=table_size)
        p1.insert_text((188.0, 257.0), "Score", fontname="tibo" if table_font == "tiro" else table_font, fontsize=table_size)
        p1.insert_text((82.0, 282.0), "Group A", fontname=table_font, fontsize=table_size)
        p1.insert_text((188.0, 282.0), "88.4", fontname=table_font, fontsize=table_size)
        p1.insert_text((76.535, 326.0), "Note. Scores aggregated at school level.", fontname="tiro", fontsize=9.0)

        # Gambar + caption gambar (juga 2 baris)
        p1.draw_rect(fitz.Rect(305.135, 210.0, 518.0, 310.0), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        p1.insert_text((305.135, 324.0), "Figure 1.", fontname="tibo" if fig_cap_font == "tiro" else fig_cap_font, fontsize=fig_cap_size)
        p1.insert_text((352.0, 324.0), "Distribution of test outcomes", fontname=fig_cap_font, fontsize=fig_cap_size)
        p1.insert_text((305.135, 335.0), "across selected cohorts.", fontname=fig_cap_font, fontsize=fig_cap_size)

        doc.save(str(path))
        doc.close()

    def test_typography_clean_standards_pass(self):
        pdf_path = self.tmp_dir / "typo_clean.pdf"
        self._build_typography_fixture(pdf_path)
        report = analyze_pdf(str(pdf_path), rules=self.rules)
        typo = [i for i in report.issues if i.category == "tipografi"]
        self.assertEqual(typo, [], [i.to_dict() for i in typo])
        self.assertEqual(report.status, "LULUS", [i.to_dict() for i in report.issues])

    def test_typography_wrong_font_family_all_four_scopes(self):
        pdf_path = self.tmp_dir / "typo_bad_family.pdf"
        self._build_typography_fixture(
            pdf_path,
            para_font="helv",
            table_font="cour",
            tbl_cap_font="helv",
            fig_cap_font="cour",
        )
        report = analyze_pdf(str(pdf_path), rules=self.rules)
        typo = [i for i in report.issues if i.category == "tipografi"]
        msgs = " | ".join(f"{i.title}: {i.message}" for i in typo).lower()
        self.assertIn("teks paragraf", msgs)
        self.assertIn("teks isian tabel", msgs)
        self.assertIn("caption tabel", msgs)
        self.assertIn("caption gambar", msgs)
        self.assertIn("helvetica", msgs)
        self.assertIn("courier", msgs)
        self.assertEqual(report.status, "PERLU PERBAIKAN")

    def test_typography_wrong_font_size_all_four_scopes(self):
        for bad_para in (10.0, 12.0):
            pdf_path = self.tmp_dir / f"typo_bad_size_{bad_para}.pdf"
            self._build_typography_fixture(
                pdf_path,
                para_size=bad_para,
                table_size=11.0,
                tbl_cap_size=11.0,
                fig_cap_size=9.0,
            )
            report = analyze_pdf(str(pdf_path), rules=self.rules)
            typo = [i for i in report.issues if i.category == "tipografi"]
            titles = " | ".join(i.title.lower() for i in typo)
            self.assertIn("teks paragraf", titles, [i.to_dict() for i in typo])
            self.assertIn("teks isian tabel", titles, [i.to_dict() for i in typo])
            self.assertIn("caption tabel", titles, [i.to_dict() for i in typo])
            self.assertIn("caption gambar", titles, [i.to_dict() for i in typo])

    def test_typography_check_can_be_disabled(self):
        pdf_path = self.tmp_dir / "typo_disabled.pdf"
        self._build_typography_fixture(
            pdf_path,
            para_font="helv",
            para_size=10.0,
            table_font="cour",
            table_size=11.0,
            tbl_cap_font="helv",
            tbl_cap_size=11.0,
            fig_cap_font="cour",
            fig_cap_size=9.0,
        )
        report = analyze_pdf(str(pdf_path), rules=JournalRules(check_fonts=False))
        typo = [i for i in report.issues if i.category == "tipografi"]
        self.assertEqual(typo, [], [i.to_dict() for i in typo])

    def test_typography_inline_table_crossref_not_false_positive(self):
        pdf_path = self.tmp_dir / "typo_inline_ref.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=595.28, height=841.89)
        p1.insert_text((76.535, 111.2), "Demographic characteristics and participant scores are presented in", fontname="tiro", fontsize=11)
        p1.insert_text((76.535, 122.2), "Table 1.", fontname="tiro", fontsize=11)
        p1.insert_text((76.535, 144.2), "Table 1.", fontname="tibo", fontsize=10)
        p1.insert_text((116.0, 144.2), "Summary of participant results", fontname="tiro", fontsize=10)
        p1.draw_rect(fitz.Rect(76.535, 152.0, 289.0, 210.0), color=(0, 0, 0), width=0.8)
        p1.draw_line((76.535, 174.0), (289.0, 174.0), color=(0, 0, 0), width=0.8)
        p1.insert_text((82.0, 166.0), "Characteristic", fontname="tibo", fontsize=10)
        p1.insert_text((82.0, 190.0), "Gender", fontname="tiro", fontsize=10)
        _add_col_lines(p1, 305.135, 111.2, 8, prefix="Right col text")
        doc.save(str(pdf_path))
        doc.close()

        report = analyze_pdf(str(pdf_path), rules=self.rules)
        typo = [i for i in report.issues if i.category == "tipografi"]
        self.assertEqual(typo, [], [i.to_dict() for i in typo])


if __name__ == "__main__":
    unittest.main(verbosity=2)
