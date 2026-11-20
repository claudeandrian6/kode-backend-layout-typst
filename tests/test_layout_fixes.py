"""Regression tests for Bug 1-4 layout fixes in build.py."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

BASE = Path(__file__).resolve().parents[1]
if not (BASE / "build.py").exists():
    BASE = Path("/opt/typst_editor")
spec = importlib.util.spec_from_file_location("layout_fixes_engine", BASE / "build.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
engine.TYPST = "/usr/local/bin/typst"


def _make_pdf_lines(path, page_specs):
    """Create a multi-page A4 PDF with lines and optional images/drawings."""
    with fitz.open() as doc:
        for lines, rects in page_specs:
            page = doc.new_page(width=595.27, height=841.89)
            for x, y, text in lines:
                page.insert_text((x, y), text, fontsize=11)
            for x0, y0, x1, y1 in rects:
                page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        doc.save(path)


class LayoutFixesTest(unittest.TestCase):
    def test_unwrap_nested_wrapper_table_sets_full_width_and_preserves_next_table_widths(self):
        inner_html = (
            "<table><thead><tr>"
            "<th>Column One Title</th><th>Column Two Title</th>"
            "<th>Column Three Title</th><th>Column Four Title</th>"
            "</tr></thead><tbody>"
            "<tr><td>Alpha data value</td><td>Beta data value</td>"
            "<td>Gamma data value</td><td>Delta data value</td></tr>"
            "</tbody></table>"
        )
        inner_ast = json.loads(subprocess.check_output(
            ["pandoc", "-f", "html", "-t", "json"], input=inner_html.encode()))
        inner_tb = inner_ast["blocks"][0]
        outer_tb = {
            "t": "Table",
            "c": [
                ["", [], []],
                [None, [{"t": "Plain", "c": [{"t": "Str", "c": "Table"}, {"t": "Space"}, {"t": "Str", "c": "5."}, {"t": "Space"}, {"t": "Str", "c": "Outer caption"}]}]],
                [[{"t": "AlignDefault"}, {"t": "ColWidthDefault"}]],
                [["", [], []], []],
                [[["", [], []], 0, [], [
                    [["", [], []], [[["", [], []], {"t": "AlignDefault"}, 1, 1, [inner_tb]]]]
                ]]],
                [["", [], []], []],
            ],
        }
        next_html = "<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
        next_tb = json.loads(subprocess.check_output(
            ["pandoc", "-f", "html", "-t", "json"], input=next_html.encode()))["blocks"][0]

        unwrapped = engine._buka_tabel_pembungkus(outer_tb)
        self.assertIsNotNone(unwrapped)
        self.assertEqual(len(unwrapped), 1)
        self.assertEqual(unwrapped[0]["t"], "Table")
        self.assertTrue(unwrapped[0]["c"][1][1])

        laporan = {"masalah": [], "peringatan": []}
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-wrap-") as td:
            blocks, stat = engine.ubah_isi([outer_tb, next_tb], td, laporan, di_tempat=True)
            typ = "".join(
                b["c"][1] if b.get("t") == "RawBlock" else engine.stringify(b)
                for b in blocks
            )
            self.assertIn("jumlah-kolom: 4, penuh: true", typ)
            self.assertEqual(len(stat["lebar_tabel"]), 2)
            self.assertEqual(len(stat["lebar_tabel"][0]), 4)
            self.assertEqual(len(stat["lebar_tabel"][1]), 2)

    def test_single_col_bottom_object_measures_zero_deficit_when_filled_above_and_flags_gap(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-1col-") as td:
            pdf_ok = Path(td) / "ok.pdf"
            lines_ok = (
                [(76.535, 111.2 + 11.0 * i, "Left line %d" % i) for i in range(40)]
                + [(305.135, 111.2 + 11.0 * i, "Right line %d" % i) for i in range(60)]
            )
            _make_pdf_lines(str(pdf_ok), [(lines_ok, [])])
            pos_ok = {
                "pm-1": (1, 1, 76.535, 102.4),
                "objek-info-1": ((1, 200.0), 1, 76.535, 562.4),
                "objek-akhir-1": ("akhir", 1, 76.535, 762.4),
            }
            teks = '#gambar("a.png", penuh: false, nomor: 1)'
            rows_ok = engine.ukur_kolom(str(pdf_ok), pos_ok, teks)
            self.assertAlmostEqual(rows_ok[0]["kurang"], 0.0, places=2)

            pdf_gap = Path(td) / "gap.pdf"
            lines_gap = (
                [(76.535, 111.2 + 11.0 * i, "Left line %d" % i) for i in range(37)]
                + [(305.135, 111.2 + 11.0 * i, "Right line %d" % i) for i in range(60)]
            )
            _make_pdf_lines(str(pdf_gap), [(lines_gap, [])])
            rows_gap = engine.ukur_kolom(str(pdf_gap), pos_ok, teks)
            self.assertGreater(rows_gap[0]["kurang"], 22.0)

    def test_segment_empty_right_detected_and_side_by_side_text_with_1col_object_passes(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-seg-") as td:
            pdf_hole = Path(td) / "hole.pdf"
            lines_hole = (
                [(76.535, 111.2 + 11.0 * i, "Left text %d" % i) for i in range(20)]
                + [(76.535, 550.0, "Table cell L"), (305.135, 550.0, "Table cell R")]
            )
            _make_pdf_lines(str(pdf_hole), [(lines_hole, []), ([], [])])
            pos_hole = {
                "pm-1": (1, 1, 76.535, 102.4),
                "objek-info-1": ((1, 300.0), 1, 76.535, 462.4),
                "objek-akhir-1": ("akhir", 1, 76.535, 762.4),
            }
            teks_hole = '#tabel(jumlah-kolom: 4, penuh: true, nomor: 1)'
            rows_hole = [x for x in engine.ukur_kolom(str(pdf_hole), pos_hole, teks_hole) if x["hal"] == 1]
            self.assertGreater(rows_hole[1]["kurang"], 100.0)
            self.assertTrue(rows_hole[1]["segmen_objek"])
            holes = engine.lubang_kolom(str(pdf_hole), pos_hole, {1: True}, teks_hole)
            self.assertTrue(any(lb["hal"] == 1 and lb["kol"] == 1 and lb["selisih"] > 100.0 for lb in holes), holes)

            pdf_side = Path(td) / "side.pdf"
            lines_side = [(305.135, 111.2 + 11.0 * i, "Right side text %d" % i) for i in range(25)]
            _make_pdf_lines(str(pdf_side), [(lines_side, [])])
            pos_side = {
                "pm-1": (1, 1, 76.535, 102.4),
                "objek-info-1": ((1, 280.0), 1, 76.535, 102.4),
                "objek-akhir-1": ("akhir", 1, 76.535, 382.4),
                "objek-info-2": ((1, 350.0), 1, 76.535, 412.4),
                "objek-akhir-2": ("akhir", 1, 76.535, 762.4),
            }
            teks_side = '#gambar("fig.png", penuh: false, nomor: 1)\n#tabel(jumlah-kolom: 4, penuh: true, nomor: 2)'
            rows_side = engine.ukur_kolom(str(pdf_side), pos_side, teks_side)
            self.assertFalse(any(x.get("segmen_objek") for x in rows_side), rows_side)
            self.assertTrue(all(abs(x["kurang"]) <= engine.ALIGN_EPS for x in rows_side), rows_side)

    def test_nilai_kandidat_layout_treats_new_violations_as_keras(self):
        def base_u(masalah):
            row = lambda k: dict(hal=1, kol=k, kurang=0.0, target=760.2,
                                 akhir_baseline=760.2, baris_terlihat=60, kosong=False)
            return dict(halaman=2, masalah=masalah, grid=[], tunggal=[], kolom=[row(0), row(1)])

        clean = engine.nilai_kandidat_layout(base_u([]), {}, 0)
        for msg in (
            "Lubang kolom kanan hlm 16 (200 pt lebih pendek dari kolom lain)",
            "Teks terjepit 5 baris di hlm 12",
            "Kolom kanan lebih panjang dari kolom kiri hlm fisik 3",
            "Halaman 4 hanya berisi 2 baris teks",
            "Kolom kanan kosong",
        ):
            score = engine.nilai_kandidat_layout(base_u([msg]), {}, 0)
            self.assertGreater(score[0], clean[0], msg)

    def test_susun_teks_and_qa_support_pindah_pita_with_target(self):
        body_dasar = (
            "#kolom[\n"
            "#pm(1);First paragraph before table.\n\n"
            "#pm(2);Short ribbon paragraph.\n\n"
            "]\n"
            "#tabel(keputusan: \"@K1@\", penuh: true)[\n"
            "  #table(columns: 2, [A], [B])\n"
            "]\n"
            "#kolom[\n"
            "#pm(3);Paragraph after table.\n"
            "]\n"
        )
        kp = {
            1: {
                "jenis": "pindah_pita",
                "target": 3,
                "mode": "putus_par",
                "n_pm": 2,
                "teks_baris": "",
            }
        }
        rendered = engine.susun_teks(body_dasar, kp)
        idx_pm1 = rendered.find("#pm(1);")
        idx_pm2 = rendered.find("#pm(2);")
        idx_pm3 = rendered.find("#pm(3);")
        idx_tbl = rendered.find("#tabel(")
        self.assertGreater(idx_tbl, idx_pm1)
        self.assertGreater(idx_pm2, idx_tbl)
        self.assertGreater(idx_pm3, idx_pm2)
        self.assertIn('keputusan: "tempat", nomor: 1', rendered)

        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-pita-") as td:
            _make_pdf_lines(str(Path(td) / "_skala.pdf"), [([], []), ([], [])])
            stat = {
                "body_dasar": body_dasar,
                "keputusan": kp,
                "label": ["Table 1."],
                "peta_objek": [(1, 1, 2)],
                "kolom_kurang": [],
                "jumlah_halaman": 2,
            }
            pos = {
                "pm-1": (1, 1, 76.535, 110.0),
                "pm-2": (2, 1, 76.535, 360.0),
                "pm-3": (1, 1, 76.535, 220.0),
                "objek-info-1": ((1, 180.0), 2, 76.535, 102.4),
                "objek-akhir-1": ("akhir", 2, 76.535, 282.4),
            }
            # Simulate shifted table appearing on page 2 while pm-2 (n_sesudah) stayed before it on page 1
            pos_shifted = dict(pos)
            pos_shifted["pm-2"] = (1, 1, 76.535, 360.0)
            masalah, peringatan, _ = engine.qa_tata_letak(
                stat, pos_shifted, td, 1, "Times New Roman", cek_font=False
            )
            self.assertFalse(any("muncul SESUDAH" in m for m in masalah), masalah)
            self.assertTrue(any("dimulai di hlm 2" in p for p in peringatan), peringatan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
