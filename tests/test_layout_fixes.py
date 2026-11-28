"""Regression tests for Bug 1-4 layout fixes in build.py."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from unittest import mock
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

    def test_mundur_sebelum_judul_and_susun_teks_keep_heading_with_paragraph(self):
        body_dasar = (
            "#kolom[\n"
            "#pm(1);First paragraph.\n\n"
            "]\n"
            "#tabel(keputusan: \"@K1@\", penuh: true)[\n"
            "  #table(columns: 2, [A], [B])\n"
            "]\n"
            "#kolom[\n"
            "#celah()\n"
            "== #pm(2);Section Heading#pe(2)\n"
            "#pm(3);Paragraph under heading.\n"
            "]\n"
        )
        for jns in ("geser", "tunda"):
            kp = {1: {"jenis": jns, "target": 3}}
            rendered = engine.susun_teks(body_dasar, kp)
            idx_tbl = rendered.find("#tabel(")
            idx_hdg = rendered.find("== #pm(2);")
            idx_par = rendered.find("#pm(3);")
            self.assertGreater(idx_hdg, idx_tbl, (jns, rendered))
            self.assertGreater(idx_par, idx_hdg, (jns, rendered))
        self.assertTrue(engine._sebelumnya_judul(body_dasar, 3))
        self.assertFalse(engine._sebelumnya_judul(body_dasar, 2))

    def test_baris_tunggal_flags_orphan_headings_above_object_and_under_two_lines(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-hdg-") as td:
            pdf1 = Path(td) / "orphan_obj.pdf"
            with fitz.open() as doc:
                pg = doc.new_page(width=595.27, height=841.89)
                for i in range(5):
                    pg.insert_text((76.535, 111.2 + 11.0 * i), f"Left line {i}", fontname="tiro", fontsize=11)
                pg.insert_text((305.135, 111.2), "Right prev line", fontname="tiro", fontsize=11)
                pg.insert_text((305.135, 133.2), "Orphan Heading Above Table", fontname="tibo", fontsize=11)
                pg.insert_text((76.535, 344.2), "Body under table", fontname="tiro", fontsize=11)
                pg.insert_text((76.535, 355.2), "Body line 2", fontname="tiro", fontsize=11)
                doc.save(str(pdf1))
            teks1 = "#pm(1);Left\n\n== #pm(2);Orphan Heading Above Table#pe(2)\n#pm(3);Body under table"
            pos1 = {
                "pm-1": (1, 1, 76.535, 102.4),
                "pm-2": (2, 1, 305.135, 124.4),
                "frag-akhir-2-0": (2, 1, 305.135, 124.4),
                "pm-3": (3, 1, 76.535, 335.4),
                "objek-info-1": ((1, 163.0), 1, 76.535, 157.4),
                "objek-akhir-1": ("akhir", 1, 76.535, 320.4),
            }
            res1 = engine.baris_tunggal(str(pdf1), pos1, teks1)
            self.assertTrue(any(d["jenis"] == "judul" and d["hal"] == 1 and d["kol"] == 1 and d["y"] < 200 for d in res1), res1)

            pdf2 = Path(td) / "short_body.pdf"
            with fitz.open() as doc:
                pg = doc.new_page(width=595.27, height=841.89)
                pg.insert_text((76.535, 111.2), "Short Heading", fontname="tibo", fontsize=11)
                pg.insert_text((76.535, 122.2), "Only one body line", fontname="tiro", fontsize=11)
                pg.insert_text((305.135, 111.2), "Split continuation line", fontname="tiro", fontsize=11)
                doc.save(str(pdf2))
            teks2 = "== #pm(1);Short Heading#pe(1)\n#pm(2);Only one body line"
            pos2 = {
                "pm-1": (1, 1, 76.535, 102.4),
                "frag-akhir-1-0": (1, 1, 76.535, 102.4),
                "pm-2": (2, 1, 76.535, 113.4),
                "frag-akhir-2-0": (2, 1, 305.135, 102.4),
            }
            res2 = engine.baris_tunggal(str(pdf2), pos2, teks2)
            self.assertTrue(any(d["jenis"] == "judul" and d["hal"] == 1 and d["kol"] == 0 and d.get("pm") == 1 for d in res2), res2)

    def test_top_floating_table_stays_on_11pt_grid(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-flt-grid-") as td:
            for jns in ("apung", "atas"):
                body = (
                    '#import "/template_jurnal.typ": *\n'
                    "#kolom[\n"
                    "#pm(1);" + ("Sample word " * 70) + "#pe(1)\n\n"
                    f'#tabel(jumlah-kolom: 2, penuh: true, di-tempat: true, keputusan: "{jns}", nomor: 1, label: [Table 1.], isi: [Grid check])[\n'
                    "#table(\n  columns: (200pt, 200pt),\n  [Header A], [Header B],\n  [Cell 1], [Cell 2],\n  [Cell 3], [Cell 4],\n)\n]\n\n"
                    "== #pm(2);Heading Below Float#pe(2)\n"
                    "#pm(3);" + ("Following text " * 90) + "#pe(3)\n"
                    "]\n"
                )
                main_typ = Path(td) / f"main_{jns}.typ"
                body_typ = Path(td) / f"body_{jns}.typ"
                meta_json = Path(td) / f"meta_{jns}.json"
                pdf_out = Path(td) / f"out_{jns}.pdf"
                body_typ.write_text(body, encoding="utf-8")
                meta_json.write_text(
                    json.dumps({
                        "judul": "Test",
                        "penulis": [{"nama": "A", "afiliasi": "1", "korespondensi": True}],
                        "afiliasi": ["Inst"],
                        "email": "a@example.com",
                        "riwayat": {},
                        "abstrak": "Abstrak.",
                        "kata_kunci": ["a", "b"],
                        "sitasi_penulis": "Author, A.",
                        "judul_kalimat": "Test",
                        "header_ganjil": "Author",
                        "terbitan": {"volume": "5", "nomor": "4", "edisi": "December", "tahun": "2026", "halaman_awal": 1},
                    }),
                    encoding="utf-8",
                )
                main_typ.write_text(
                    '#import "/template_jurnal.typ": *\n'
                    '#let cfg = json("/jurnal/bsscd/config.json")\n'
                    f'#let meta = json("{meta_json.name}")\n'
                    '#show: artikel.with(cfg, meta)\n'
                    f'#include "{body_typ.name}"\n',
                    encoding="utf-8",
                )
                subprocess.run([engine.TYPST, "compile", "--root", str(BASE), str(main_typ), str(pdf_out)], check=True)
                pos, _ = engine.kueri_posisi(str(main_typ))
                grid = engine.baris_luar_grid(str(pdf_out), pos, body)
                self.assertEqual(grid, [], f"keputusan={jns} off-grid: {grid}")

    def test_cari_potong_refines_existing_split_paragraph(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-cutref-") as td:
            pdf = Path(td) / "split.pdf"
            lines_p1 = (
                [(76.535, 111.2 + 11.0 * i, f"Left line number {i} extra words") for i in range(6)]
                + [(305.135, 111.2, "Right line 0"), (305.135, 122.2, "Right line 1")]
            )
            lines_p2 = [(76.535, 111.2 + 11.0 * i, f"Page two line {i}") for i in range(4)]
            _make_pdf_lines(str(pdf), [(lines_p1, []), (lines_p2, [])])
            teks_dasar = (
                "#kolom[\n"
                "#pm(1);" + " ".join(f"Left line number {i} extra words" for i in range(6)) + "#pe(1)\n\n"
                "#pm(2);Page two line 0 Page two line 1 Page two line 2 Page two line 3#pe(2)\n"
                "]\n"
            )
            teks_split = engine.potong_paragraf(teks_dasar, 1, "Left line number 4 extra words")
            self.assertIsNotNone(teks_split)
            pos = {
                "pm-1": (1, 1, 76.535, 102.4),
                "pm-2": (2, 1, 305.135, 102.4),
            }
            self.assertIsNone(engine.cari_potong(str(pdf), pos, 1, 0, 2, teks_split))
            refined = engine.cari_potong(str(pdf), pos, 1, 0, 2, teks_split, teks_dasar=teks_dasar)
            self.assertEqual(refined, ("belah", 1, "Left line number 4 extra words"))

    def test_compact_table_deferred_as_tunda_not_geser(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-tbl-tunda-") as td:
            td_p = Path(td)
            body_dasar = (
                '#import "/template_jurnal.typ": *\n'
                "#kolom[\n"
                "#pm(1);" + ("Introductory sentence before table on the first page. " * 100) + "#pe(1)\n\n"
                "#pm(2);" + ("Second paragraph filling the page right up to the compact table. " * 50) + "#pe(2)\n\n"
                "]\n"
                '#tabel(jumlah-kolom: 2, penuh: true, kepala: 2, di-tempat: true, keputusan: "@K1@", label: [Table 1.], isi: [Compact table])[\n'
                "#table(\n"
                "  columns: (200pt, 200pt),\n"
                "  [Col A], [Col B],\n"
                + "".join(f"  [Val {i}A], [Val {i}B],\n" for i in range(1, 11))
                + ")\n"
                "]\n"
                "#kolom[\n"
                "#pm(3);" + ("Following paragraph after table. " * 35) + "#pe(3)\n\n"
                "#pm(4);" + ("Second following paragraph on page two. " * 30) + "#pe(4)\n"
                "]\n"
            )
            body_path = td_p / "body.typ"
            meta_json = td_p / "meta.json"
            main_typ = td_p / "main.typ"
            pdf_out = td_p / "out.pdf"
            body_path.write_text(body_dasar, encoding="utf-8")
            meta_json.write_text(
                json.dumps({
                    "judul": "Compact Table Deferral Test",
                    "penulis": [{"nama": "Author", "afiliasi": "1", "korespondensi": True}],
                    "afiliasi": ["Inst"],
                    "email": "a@example.com",
                    "riwayat": {},
                    "abstrak": "Abstract paragraph.",
                    "kata_kunci": ["a", "b"],
                    "sitasi_penulis": "Author, A.",
                    "judul_kalimat": "Compact table deferral test",
                    "header_ganjil": "Author, Compact",
                    "terbitan": {"volume": "5", "nomor": "4", "edisi": "December", "tahun": "2026", "halaman_awal": 1},
                }),
                encoding="utf-8",
            )
            rel_td = td_p.relative_to(BASE).as_posix()
            main_typ.write_text(
                '#import "/template_jurnal.typ": *\n'
                '#let cfg = json("/jurnal/bsscd/config.json")\n'
                f'#let meta = json("/{rel_td}/meta.json")\n'
                '#show: artikel.with(cfg, meta)\n'
                '#include "body.typ"\n',
                encoding="utf-8",
            )
            kp, log_p = engine.putuskan_objek(
                body_dasar, str(body_path), str(td_p), str(pdf_out), {1: {"jenis": "tempat"}}, [(1, 2, 3)]
            )
            self.assertEqual(kp[1]["jenis"], "tunda", (kp, log_p))
            self.assertFalse(kp[1].get("maju"), (kp, log_p))
            self.assertTrue(any("Tabel 1 -> tunda" in m for m in log_p), log_p)

    def test_citation_to_intro_gap_is_22pt_across_all_header_grids(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-cite-gap-") as td:
            td_p = Path(td)
            body_typ = td_p / "body.typ"
            meta_json = td_p / "meta.json"
            body_typ.write_text(
                '#import "/template_jurnal.typ": *\n'
                "#kolom[\n"
                "= #pm(1);INTRODUCTION#pe(1)\n"
                "#pm(2);" + ("Introductory paragraph text for citation spacing check. " * 25) + "#pe(2)\n"
                "]\n",
                encoding="utf-8",
            )
            meta_json.write_text(
                json.dumps({
                    "judul": "Citation Spacing Verification Test Across Grid Offsets",
                    "penulis": [{"nama": "Author One", "afiliasi": "1", "korespondensi": True}],
                    "afiliasi": ["Test University"],
                    "email": "a@example.com",
                    "riwayat": {},
                    "abstrak": "Short abstract paragraph for testing citation-to-intro spacing.",
                    "kata_kunci": ["layout", "grid", "spacing"],
                    "sitasi_penulis": "One, A.",
                    "judul_kalimat": "Citation spacing verification test",
                    "header_ganjil": "One, Citation",
                    "terbitan": {"volume": "5", "nomor": "4", "edisi": "December", "tahun": "2026", "halaman_awal": 1},
                }),
                encoding="utf-8",
            )
            rel_td = td_p.relative_to(BASE).as_posix()
            intro_ys = {}
            for hg in (-22, -11, 0, 11, 22):
                main_typ = td_p / f"main_{hg}.typ"
                pdf_out = td_p / f"out_{hg}.pdf"
                main_typ.write_text(
                    '#import "/template_jurnal.typ": *\n'
                    '#let cfg = json("/jurnal/bsscd/config.json")\n'
                    f'#let meta = json("/{rel_td}/meta.json")\n'
                    f'#show: artikel.with(cfg, meta, header-grid: {hg}pt)\n'
                    '#include "body.typ"\n',
                    encoding="utf-8",
                )
                subprocess.run([engine.TYPST, "compile", "--root", str(BASE), str(main_typ), str(pdf_out)], check=True)
                with fitz.open(str(pdf_out)) as doc:
                    pg = doc[0]
                    draws = [d["rect"] for d in pg.get_drawings() if d["rect"].width > 400 and d["rect"].y0 > 200]
                    cite_rule_y1 = max(float(r.y1) for r in draws)
                    intro = [
                        l for b in pg.get_text("dict")["blocks"] for l in b.get("lines", [])
                        if "INTRODUCTION" in "".join(s["text"] for s in l["spans"])
                    ]
                    self.assertTrue(intro)
                    intro_y0 = float(intro[0]["bbox"][1])
                    gap = intro_y0 - cite_rule_y1
                    self.assertLessEqual(abs(gap - 22.0), 1.5, f"hg={hg}: gap={gap}")
                    intro_ys[hg] = intro_y0
            self.assertAlmostEqual(intro_ys[0] - intro_ys[-11], 11.0, places=1)
            self.assertAlmostEqual(intro_ys[-11] - intro_ys[-22], 11.0, places=1)
            self.assertAlmostEqual(intro_ys[11] - intro_ys[0], 11.0, places=1)
            self.assertAlmostEqual(intro_ys[22] - intro_ys[11], 11.0, places=1)

    def test_trapped_middle_ribbon_pushes_midpage_table_to_bottom(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-sandw-bawah-") as td:
            pdf_mid = Path(td) / "mid.pdf"
            lines_mid = (
                [(76.535, 309.2 + 11.0 * i, f"Mid line L {i}") for i in range(8)]
                + [(305.135, 309.2 + 11.0 * i, f"Mid line R {i}") for i in range(8)]
                + [(76.535, 518.2 + 11.0 * i, f"Bot line L {i}") for i in range(10)]
                + [(305.135, 518.2 + 11.0 * i, f"Bot line R {i}") for i in range(10)]
            )
            _make_pdf_lines(str(pdf_mid), [(lines_mid, [])])
            pos_mid = {
                "objek-info-1": ((1, 180.0), 1, 76.535, 102.4),
                "objek-akhir-1": ("akhir", 1, 76.535, 282.4),
                "objek-info-2": ((2, 95.0), 1, 76.535, 398.4),
                "objek-akhir-2": ("akhir", 1, 76.535, 493.4),
            }
            pita_mid = engine.pita_kecil(str(pdf_mid), pos_mid, {1: True, 2: True})
            self.assertEqual(len(pita_mid), 1, pita_mid)
            self.assertEqual(pita_mid[0]["letak"], "tengah")
            self.assertEqual(pita_mid[0]["baris"], 8)
            self.assertTrue(pita_mid[0]["ada_bawah"])
            self.assertEqual(pita_mid[0]["batas_min"], engine.PITA_TERJEPIT_TENGAH_MIN)

            pdf_bot = Path(td) / "bot.pdf"
            lines_bot = (
                [(76.535, 309.2 + 11.0 * i, f"Mid line L {i}") for i in range(8)]
                + [(305.135, 309.2 + 11.0 * i, f"Mid line R {i}") for i in range(8)]
            )
            _make_pdf_lines(str(pdf_bot), [(lines_bot, [])])
            pos_bot = {
                "objek-info-1": ((1, 180.0), 1, 76.535, 102.4),
                "objek-akhir-1": ("akhir", 1, 76.535, 282.4),
                "objek-info-2": ((2, 120.0), 1, 76.535, 622.4),
                "objek-akhir-2": ("akhir", 1, 76.535, 742.4),
            }
            self.assertEqual(engine.pita_kecil(str(pdf_bot), pos_bot, {1: True, 2: True}), [])

    def test_baris_tunggal_does_not_flag_complete_one_line_paragraph_under_heading(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-hdg-1ln-") as td:
            pdf = Path(td) / "complete_one_liner.pdf"
            with fitz.open() as doc:
                pg = doc.new_page(width=595.27, height=841.89)
                pg.insert_text((305.135, 639.2), "Previous paragraph tail.", fontname="tiro", fontsize=11)
                pg.insert_text((330.135, 661.2), "CONFLICT OF INTEREST", fontname="tibo", fontsize=11)
                pg.insert_text((305.135, 672.2), "The authors declare no conflict of interest.", fontname="tiro", fontsize=11)
                pg.insert_text((330.135, 694.2), "ETHICS STATEMENT", fontname="tibo", fontsize=11)
                pg.insert_text((305.135, 705.2), "Institutional approval was obtained.", fontname="tiro", fontsize=11)
                doc.save(str(pdf))
            teks = (
                "#pm(69);Previous paragraph tail.#pe(69)\n\n"
                "#celah()\n"
                "= #pm(70);CONFLICT OF INTEREST#pe(70)\n"
                "#pm(71);The authors declare no conflict of interest.#pe(71)\n\n"
                "#celah()\n"
                "= #pm(72);ETHICS STATEMENT#pe(72)\n"
                "#pm(73);Institutional approval was obtained.#pe(73)\n"
            )
            # Typst baseline positions for #pm/#pe
            pos = {
                "pm-69": (69, 1, 305.135, 630.4), "frag-akhir-69-0": ([69, 0], 1, 420.0, 639.2),
                "pm-70": (70, 1, 330.135, 652.4), "frag-akhir-70-0": ([70, 0], 1, 464.0, 661.2),
                "pm-71": (71, 1, 305.135, 663.4), "frag-akhir-71-0": ([71, 0], 1, 516.7, 672.2),
                "pm-72": (72, 1, 330.135, 685.4), "frag-akhir-72-0": ([72, 0], 1, 441.8, 694.2),
                "pm-73": (73, 1, 305.135, 696.4), "frag-akhir-73-0": ([73, 0], 1, 490.0, 705.2),
            }
            res = engine.baris_tunggal(str(pdf), pos, teks)
            self.assertFalse(any(d.get("pm") == 70 for d in res), res)

    def test_calon_objek_ketat_generates_wider_deltas_and_multi_image_pairs(self):
        body = (
            '#kolom[\n#pm(1);First paragraph.#pe(1)\n]\n'
            '#gambar("media/fig1.png", lebar-word: 350.0pt, di-tempat: true, keputusan: "@K1@", nomor: 1, label: [Figure 1.], isi: [Cap 1])\n'
            '#kolom[\n#pm(2);Middle paragraph.#pe(2)\n]\n'
            '#gambar("media/fig2.png", lebar-word: 350.0pt, di-tempat: true, keputusan: "@K2@", nomor: 2, label: [Figure 2.], isi: [Cap 2])\n'
            '#kolom[\n#pm(3);Last paragraph.#pe(3)\n]\n'
        )
        kp = {1: {"jenis": "tunda", "target": 2, "skala": 0.90}, 2: {"jenis": "tunda", "target": 3, "skala": 0.90}}
        pos = {
            "objek-info-1": ((1, 300.0), 3, 76.535, 102.4),
            "objek-info-2": ((2, 300.0), 4, 76.535, 102.4),
        }
        u = {
            "halaman": 5,
            "kolom": [{"hal": 4, "kol": 0, "kurang": 22.0, "kosong": False, "baris_terlihat": 20}],
            "tunggal": [],
            "grid": [],
            "masalah": [],
        }
        with mock.patch.object(engine, "ukuran_gambar", return_value=(442.2, 300.0, 2400, 1600)):
            cands = list(engine.calon_objek_ketat(body, kp, pos, u))
        joint = [c for c in cands if c[1].get("skala") != 0.90 and c[2].get("skala") != 0.90]
        self.assertTrue(joint, "Expected joint 2-image scaling candidates")
        self.assertTrue(any(c[1]["skala"] < 0.90 and c[2]["skala"] > 0.90 for c in joint), joint)
        single_k1_scales = [c[1]["skala"] for c in cands if c[1].get("skala") != 0.90 and c[2].get("skala") == 0.90]
        self.assertGreaterEqual(len(single_k1_scales), 6, single_k1_scales)

    def test_tarik_kata_word_stealing_preserves_words_markup_and_qa(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-tarik-") as td:
            td_p = Path(td)
            p1 = (
                "Recent empirical studies demonstrate that structured collaborative reading tasks consistently support "
                "narrative comprehension gains across secondary learners in urban classrooms. " * 7
            ).strip()
            p2 = (
                "Furthermore, teachers observed that sequential discussion prompts enabled students to integrate literal "
                "details with inferential reasoning throughout every instructional unit across the entire twelve-week intervention. " * 5
            ).strip()
            body_raw = f'#import "/template_jurnal.typ": *\n#kolom-lanjut[\n#pm(1);{p1}#pe(1)\n\n#pm(2);{p2}#pe(2)\n]\n'
            self.assertIsNone(engine.tarik_kata_paragraf('#pm(1);Short.#pe(1)\n\n#pm(2);Too short tail here.#pe(2)', 1, 2, 2))
            steal = engine.tarik_kata_paragraf(body_raw, 1, 2, 5)
            self.assertIsNotNone(steal)
            self.assertIn("#linebreak(justify: true)", steal)
            self.assertIn("#colbreak()\n#lanjutan[#pm(2);", steal)
            self.assertEqual(engine.terapkan_potong(body_raw, ("tarik", 1, 2, 5)), steal)

            # Real Typst compile + grid + fragment QA verification
            body_typ = td_p / "body.typ"
            meta_json = td_p / "meta.json"
            main_typ = td_p / "main.typ"
            pdf_out = td_p / "out.pdf"
            body_typ.write_text(steal, encoding="utf-8")
            meta_json.write_text(
                json.dumps({
                    "judul": "Word Stealing Test",
                    "penulis": [{"nama": "A", "afiliasi": "1", "korespondensi": True}],
                    "afiliasi": ["Inst"],
                    "email": "a@example.com",
                    "riwayat": {},
                    "abstrak": "Abstract.",
                    "kata_kunci": ["a", "b"],
                    "sitasi_penulis": "A",
                    "judul_kalimat": "Word stealing test",
                    "header_ganjil": "A",
                    "terbitan": {"volume": "5", "nomor": "4", "edisi": "December", "tahun": "2026", "halaman_awal": 1},
                }),
                encoding="utf-8",
            )
            rel_td = td_p.relative_to(BASE).as_posix()
            main_typ.write_text(
                '#import "/template_jurnal.typ": *\n#let cfg = json("/jurnal/bsscd/config.json")\n'
                f'#let meta = json("/{rel_td}/meta.json")\n#show: artikel.with(cfg, meta)\n#include "body.typ"\n',
                encoding="utf-8",
            )
            subprocess.run([engine.TYPST, "compile", "--root", str(BASE), str(main_typ), str(pdf_out)], check=True)
            pos, _ = engine.kueri_posisi(str(main_typ))
            self.assertEqual(engine.baris_luar_grid(str(pdf_out), pos, steal), [])
            tunggal, galat = engine.fragmen_paragraf_ketat(str(pdf_out), pos, steal)
            self.assertEqual((tunggal, galat), ([], []))
            with fitz.open(str(pdf_out)) as doc:
                pdf_words = " ".join(pg.get_text() for pg in doc).lower()
            for w in ("furthermore", "teachers", "observed", "comprehension"):
                self.assertIn(w, pdf_words)

    def test_susun_teks_tunda_after_table_preserves_table_closing_bracket(self):
        body_dasar = (
            "#kolom[\n"
            "#pm(1);First paragraph before table.#pe(1)\n"
            "]\n"
            '#tabel(jumlah-kolom: 2, penuh: true, kepala: 1, di-tempat: true, keputusan: "@K1@", label: [Table 1.], isi: [Tbl])[\n'
            "#table(\n"
            "  columns: (100pt, 100pt),\n"
            "  table.header(table.cell(x: 0, y: 0)[A], table.cell(x: 1, y: 0)[B]),\n"
            "  table.cell(x: 0, y: 1)[1], table.cell(x: 1, y: 1)[2],\n"
            ")\n"
            "]\n"
            '#gambar("/media/fig.png", lebar-word: 400.0pt, di-tempat: true, keputusan: "@K2@", label: [Figure 1.], isi: [Cap])\n'
            "#kolom[\n"
            "== #pm(2);Section Heading#pe(2)\n"
            "#pm(3);Text immediately after table.#pe(3)\n"
            "#pm(4);More text later.#pe(4)\n"
            "]\n"
        )
        res = engine.susun_teks(
            body_dasar,
            {1: {"jenis": "tempat"}, 2: {"jenis": "tunda", "target": 4}},
            akhir_seimbang=True,
        )
        tbl_idx = res.index("#tabel(")
        tbl_open = res.index(")[\n", tbl_idx) + 1
        tbl_close = engine.akhir_kurung_typst(res, tbl_open)
        self.assertIsNotNone(tbl_close)
        after_tbl = res[tbl_close:tbl_close + 12]
        self.assertTrue(after_tbl.startswith("]\n#kolom["), after_tbl)
        kol_open = tbl_close + len("]\n#kolom")
        kol_close = engine.akhir_kurung_typst(res, kol_open)
        self.assertIsNotNone(kol_close)
        kol_body = res[kol_open:kol_close]
        self.assertIn("#pm(2);", kol_body)
        self.assertIn("#pm(3);", kol_body)
        self.assertIn('nomor: 2', kol_body)
        self.assertLess(kol_body.index('nomor: 2'), kol_body.index("#pm(4);"))

    def test_calon_objek_ketat_prioritizes_hard_errors_over_valid_11pt_right(self):
        body = (
            '#kolom[\n#pm(1);First.#pe(1)\n]\n'
            '#gambar("media/f1.png", lebar-word: 350.0pt, di-tempat: true, keputusan: "@K1@", nomor: 1, label: [Figure 1.], isi: [C1])\n'
            '#kolom[\n#pm(2);Middle.#pe(2)\n]\n'
            '#gambar("media/f2.png", lebar-word: 350.0pt, di-tempat: true, keputusan: "@K2@", nomor: 2, label: [Figure 2.], isi: [C2])\n'
            '#kolom[\n#pm(3);Last.#pe(3)\n]\n'
        )
        kp = {1: {"jenis": "atas", "skala": 0.90}, 2: {"jenis": "tunda", "target": 3, "skala": 0.90}}
        pos = {
            "objek-info-1": ((1, 250.0), 2, 76.535, 102.4),
            "objek-info-2": ((2, 300.0), 7, 76.535, 102.4),
        }
        u = {
            "halaman": 10,
            "kolom": [
                {"hal": 1, "kol": 1, "kurang": 11.0, "kosong": False, "baris_terlihat": 20},
                {"hal": 7, "kol": 0, "kurang": 44.0, "kosong": False, "baris_terlihat": 10},
            ],
            "tunggal": [],
            "grid": [],
            "masalah": ["Kolom kiri hlm fisik 7: sisa 44.00 pt (target 0 pt)"],
        }
        orig_awal = engine.HALAMAN_AWAL_CETAK[0]
        try:
            engine.HALAMAN_AWAL_CETAK[0] = None
            with mock.patch.object(engine, "ukuran_gambar", return_value=(442.2, 300.0, 2400, 1600)):
                cands = list(engine.calon_objek_ketat(body, kp, pos, u))
        finally:
            engine.HALAMAN_AWAL_CETAK[0] = orig_awal
        first_changed_k = next(k for k in kp if cands[0][k] != kp[k])
        self.assertEqual(first_changed_k, 2, cands[:3])

    def test_lubang_kolom_detects_hole_between_floating_top_and_bottom_objects(self):
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-lubang-float-") as td:
            pdf = Path(td) / "lubang_float.pdf"
            lines_p1 = (
                [(76.535, 243.2 + 11.0 * i, f"Left line {i}") for i in range(32)]
                + [(305.135, 243.2 + 11.0 * i, f"Right line {i}") for i in range(5)]
            )
            lines_p2 = (
                [(76.535, 111.2 + 11.0 * i, f"P2 L {i}") for i in range(10)]
                + [(305.135, 111.2 + 11.0 * i, f"P2 R {i}") for i in range(10)]
            )
            _make_pdf_lines(str(pdf), [(lines_p1, []), (lines_p2, [])])
            pos = {
                "objek-info-1": ((1, 121.0), 1, 76.535, 102.4),
                "objek-info-2": ((2, 136.0), 1, 76.535, 611.4),
            }
            lubang = engine.lubang_kolom(str(pdf), pos, {1: True, 2: True})
            self.assertEqual(len(lubang), 1, lubang)
            self.assertEqual(lubang[0]["hal"], 1)
            self.assertEqual(lubang[0]["kol"], 1)
            self.assertGreater(lubang[0]["selisih"], 250.0)

    def test_tabel_lintas_sejati_allows_floating_compact_split_table(self):
        rows_4 = "\n".join(f"  table.cell(x: 0, y: {r})[R{r}], table.cell(x: 1, y: {r})[V{r}]," for r in range(5))
        rows_16 = "\n".join(f"  table.cell(x: 0, y: {r})[R{r}], table.cell(x: 1, y: {r})[V{r}]," for r in range(16))
        body = (
            '#tabel(jumlah-kolom: 2, penuh: true, kepala: 1, di-tempat: true, keputusan: "@K1@", label: [Table 1.], isi: [Compact])[\n'
            f"#table(\n  columns: (100pt, 100pt),\n{rows_4}\n)\n]\n"
            '#tabel(jumlah-kolom: 2, penuh: true, kepala: 1, di-tempat: true, keputusan: "@K2@", label: [Table 2.], isi: [Long])[\n'
            f"#table(\n  columns: (100pt, 100pt),\n{rows_16}\n)\n]\n"
        )
        self.assertFalse(engine._tabel_lintas_sejati(body, 1, ([1, 0.0], 8, 76.535, 663.4), ("akhir", 9, 76.535, 151.4)))
        self.assertTrue(engine._tabel_lintas_sejati(body, 2, ([2, 0.0], 8, 76.535, 120.0), ("akhir", 9, 76.535, 300.0)))
        self.assertTrue(engine._tabel_lintas_sejati(body, 1, ([1, 0.0], 8, 76.535, 120.0), ("akhir", 9, 76.535, 300.0)))

    def test_spesifikasi_potong_layout_prioritizes_hard_error_page_over_valid_early_page(self):
        u = {
            "halaman": 15,
            "kolom": [
                {"hal": 2, "kol": 1, "kurang": 11.0, "kosong": False, "baris_terlihat": 58},
                {"hal": 14, "kol": 0, "kurang": 11.0, "kosong": False, "baris_terlihat": 59},
            ],
            "tunggal": [],
            "masalah": ["Kolom kiri hlm fisik 14: sisa 11.00 pt (target 0 pt)"],
        }
        seen_calls = []

        def fake_cari_potong(pdf, pos, h, kol, m, teks, teks_dasar=None):
            seen_calls.append((h, kol, m))
            return ("putus", h * 100 + kol * 10 + m) if m == 1 else None

        orig_awal = engine.HALAMAN_AWAL_CETAK[0]
        try:
            engine.HALAMAN_AWAL_CETAK[0] = None
            with mock.patch.object(engine, "cari_potong", side_effect=fake_cari_potong):
                specs = list(engine.spesifikasi_potong_layout("teks", "fake.pdf", {}, u))
        finally:
            engine.HALAMAN_AWAL_CETAK[0] = orig_awal
        self.assertTrue(specs)
        queried_pages = {c[0] for c in seen_calls}
        self.assertEqual(queried_pages, {13, 14})
        self.assertEqual(seen_calls[:4], [(13, 0, 1), (13, 1, 1), (14, 0, 1), (14, 1, 1)])

    def test_susun_teks_tunda_letak_bawah_emits_bawah_keyword(self):
        body_dasar = (
            "#kolom[\n"
            "#pm(1);First paragraph.#pe(1)\n"
            "]\n"
            '#tabel(jumlah-kolom: 2, penuh: true, kepala: 1, di-tempat: true, keputusan: "@K1@", label: [Table 1.], isi: [T1])[\n'
            "#table(columns: (100pt, 100pt), [A], [B], [1], [2])\n"
            "]\n"
            '#tabel(jumlah-kolom: 2, penuh: true, kepala: 1, di-tempat: true, keputusan: "@K2@", label: [Table 2.], isi: [T2])[\n'
            "#table(columns: (100pt, 100pt), [C], [D], [3], [4])\n"
            "]\n"
            "#kolom[\n"
            "#pm(2);Target paragraph after objects.#pe(2)\n"
            "]\n"
        )
        res = engine.susun_teks(
            body_dasar,
            {
                1: {"jenis": "tunda", "target": 2},
                2: {"jenis": "tunda", "target": 2, "letak": "bawah"},
            },
            akhir_seimbang=True,
        )
        self.assertIn('keputusan: "apung", nomor: 1', res)
        self.assertIn('keputusan: "bawah", nomor: 2', res)
        self.assertLess(res.index('nomor: 1'), res.index("#pm(2);"))
        self.assertLess(res.index('nomor: 2'), res.index("#pm(2);"))

    def test_table_body_and_captions_render_exact_10pt_on_11pt_grid(self):
        import editor_blok
        from PIL import Image

        self.assertEqual(engine.UKURAN_TABEL_PT, 10.0)
        self.assertEqual(editor_blok.gaya_tabel_bawaan()["font_size"], 10.0)

        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-font10-") as td:
            td_p = Path(td)
            rel_td = td_p.relative_to(BASE).as_posix()
            img_path = td_p / "sample_fig.png"
            Image.new("RGB", (200, 100), color=(210, 220, 230)).save(img_path)

            body = (
                '#import "/template_jurnal.typ": *\n'
                "#kolom[\n"
                "#pm(1);First narrative body line " + ("word " * 30) + "#pe(1)\n\n"
                '#tabel(jumlah-kolom: 2, penuh: true, di-tempat: true, keputusan: "tempat", nomor: 1, label: [Table 1.], isi: [Table caption sample])[\n'
                "#table(\n  columns: (200pt, 200pt),\n  [HeaderAlpha], [HeaderBeta],\n  [CellAlpha], [CellBeta],\n)\n]\n\n"
                f'#gambar("/{rel_td}/{img_path.name}", lebar-word: 250pt, penuh: false, di-tempat: true, keputusan: "tempat", nomor: 2, label: [Figure 1.], isi: [Figure caption sample])\n\n'
                "#pm(2);Narrative below objects " + ("text " * 50) + "#pe(2)\n"
                "]\n"
            )
            main_typ = td_p / "main.typ"
            body_typ = td_p / "body.typ"
            meta_json = td_p / "meta.json"
            pdf_out = td_p / "out.pdf"
            body_typ.write_text(body, encoding="utf-8")
            meta_json.write_text(
                json.dumps({
                    "judul": "Font 10pt Test",
                    "penulis": [{"nama": "Author One", "afiliasi": "1", "korespondensi": True}],
                    "afiliasi": ["Inst"],
                    "email": "a@example.com",
                    "riwayat": {},
                    "abstrak": "Abstrak pengujian ukuran font.",
                    "kata_kunci": ["font", "grid"],
                    "sitasi_penulis": "Author, A.",
                    "judul_kalimat": "Font 10pt Test",
                    "header_ganjil": "Author",
                    "terbitan": {"volume": "5", "nomor": "4", "edisi": "December", "tahun": "2026", "halaman_awal": 1},
                }),
                encoding="utf-8",
            )
            main_typ.write_text(
                '#import "/template_jurnal.typ": *\n'
                '#let cfg = json("/jurnal/bsscd/config.json")\n'
                f'#let meta = json("/{rel_td}/{meta_json.name}")\n'
                '#show: artikel.with(cfg, meta)\n'
                f'#include "/{rel_td}/{body_typ.name}"\n',
                encoding="utf-8",
            )
            subprocess.run(
                [engine.TYPST, "compile", "--root", str(BASE), "--font-path", str(BASE / "fonts"), str(main_typ), str(pdf_out)],
                check=True,
                capture_output=True,
            )
            pos, _ = engine.kueri_posisi(str(main_typ))
            grid = engine.baris_luar_grid(str(pdf_out), pos, body)
            self.assertEqual(grid, [], f"Narrative below 10pt table went off 11pt grid: {grid}")

            span_sizes = {}
            with fitz.open(str(pdf_out)) as doc:
                for pg in doc:
                    for blk in pg.get_text("dict").get("blocks", []):
                        for ln in blk.get("lines", []):
                            for sp in ln.get("spans", []):
                                t = sp.get("text", "").strip()
                                if t:
                                    span_sizes.setdefault(t, []).append(round(float(sp["size"]), 2))

            for token, expected in (
                ("Table 1.", 10.0),
                ("Table caption sample", 10.0),
                ("HeaderAlpha", 10.0),
                ("CellAlpha", 10.0),
                ("Figure 1.", 10.0),
                ("Figure caption sample", 10.0),
            ):
                matched = [sz for txt, szs in span_sizes.items() if token in txt for sz in szs]
                self.assertTrue(matched, f"Token {token!r} not found in compiled PDF")
                for sz in matched:
                    self.assertAlmostEqual(sz, expected, delta=0.1, msg=f"{token}: got {sz}")

            body_matched = [sz for txt, szs in span_sizes.items() if "Narrative below objects" in txt for sz in szs]
            self.assertTrue(body_matched)
            for sz in body_matched:
                self.assertAlmostEqual(sz, 11.0, delta=0.1)

    def test_hypothesis_detection_and_false_positive_guards(self):
        def para_str(txt):
            return {"t": "Para", "c": [{"t": "Str", "c": txt}]}

        blocks = [
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "INTRODUCTION"}]]},
            para_str("Introductory narrative paragraph with normal indentation."),
            para_str("H1: Transformational leadership positively affects organizational trust."),
            para_str("H1 was supported in previous meta-analytic studies across sectors."),
            para_str("H2. Digital competence positively affects teacher performance."),
            {"t": "Para", "c": [{"t": "Strong", "c": [{"t": "Str", "c": "H3: Self-efficacy improves learning motivation"}]}]},
            para_str("H4 \u2013 Perceived usefulness predicts technology adoption among teachers."),
            para_str("H1 \u2013 H5 were tested using structural equation modeling in Amos."),
            para_str("H2O is a chemical compound rather than a hypothesis."),
            para_str("H6: Organizational culture mediates leadership and innovation."),
            para_str("H7: Peer collaboration moderates training transfer."),
            para_str("Hypothesis 8: Work engagement predicts employee retention."),
            para_str("QA1: How do principals support curriculum innovation?"),
            para_str("QA2: What barriers emerge during implementation?"),
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "REFERENCES"}]]},
            para_str("H1: Reference entry inside references section (2024). Journal, 1(1), 1-10."),
        ]
        laporan = {"masalah": [], "peringatan": [], "normalisasi": []}
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-hyp-ast-") as td:
            out_blocks, _ = engine.ubah_isi(blocks, td, laporan, di_tempat=True)
            ast_doc = {"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": out_blocks}
            proc = subprocess.run(
                [engine.PANDOC, "-f", "json", "-t", "typst", "--wrap=none"],
                input=json.dumps(ast_doc).encode("utf-8"),
                capture_output=True,
                check=True,
            )
            typ_txt = proc.stdout.decode("utf-8")

        self.assertEqual(laporan["masalah"], [], laporan)
        lines = [ln.strip() for ln in typ_txt.splitlines() if ln.strip()]

        def line_idx(substr):
            for idx, ln in enumerate(lines):
                if substr in ln:
                    return idx
            self.fail(f"{substr!r} not found in Typst output:\n{typ_txt}")

        # Standalone dan berurutan H1..H4, H6, H7 menjadi #label-gantung + #celah() di atasnya
        for lbl, snippet in (
            ("[H1:]", "Transformational leadership"),
            ("[H2.]", "Digital competence"),
            ("[H3:]", "Self-efficacy"),
            ("[H4 \u2013]", "Perceived usefulness"),
            ("[H6:]", "Organizational culture"),
            ("[H7:]", "Peer collaboration"),
        ):
            idx = line_idx(snippet)
            self.assertIn(f"#label-gantung({lbl})", lines[idx])
            self.assertGreater(idx, 0)
            self.assertEqual(lines[idx - 1], "#celah()", f"Missing #celah() above {lbl}: {lines[idx - 1:idx + 1]}")

        # Hypothesis 8: berbentuk paragraf tanpa indent (#h(-25pt)) + #celah() di atasnya
        idx_h8 = line_idx("Work engagement predicts employee retention.")
        self.assertIn("#h(-25pt)", lines[idx_h8])
        self.assertEqual(lines[idx_h8 - 1], "#celah()")

        # Guard false-positive: narasi biasa, rentang H1 - H5, rumus H2O, dan daftar QA tidak rusak
        for prose in (
            "H1 was supported in previous",
            "H1 \u2013 H5 were tested",
            "H2O is a chemical compound",
        ):
            idx_p = line_idx(prose)
            self.assertNotIn("#label-gantung", lines[idx_p])
            self.assertNotIn("#h(-25pt)", lines[idx_p])
            self.assertNotEqual(lines[idx_p - 1], "#celah()")

        idx_qa1 = line_idx("How do principals support")
        self.assertIn("#label-gantung([QA1:])", lines[idx_qa1])
        self.assertNotEqual(lines[idx_qa1 - 1], "#celah()")

        idx_ref = line_idx("Reference entry inside references")
        self.assertIn("#referensi[", lines[idx_ref])
        self.assertNotIn("#label-gantung", lines[idx_ref])

    def test_hypothesis_rendered_pdf_no_indent_11pt_gap_and_on_grid(self):
        import editor_blok

        def para_str(txt):
            return {"t": "Para", "c": [{"t": "Str", "c": txt}]}

        blocks = [
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "INTRODUCTION"}]]},
            para_str(
                "This introductory narrative paragraph discusses prior findings before formulating the hypotheses. "
                * 2
            ),
            para_str("H1: Transformational leadership has a positive effect on teacher trust in schools."),
            para_str(
                "Following the first hypothesis, another body paragraph elaborates the structural model and variables. "
                * 2
            ),
            para_str("H6: Organizational culture mediates leadership and innovation in public institutions."),
            para_str("H7: Digital literacy moderates training and employee performance across departments."),
            para_str("Hypothesis 8: Psychological safety encourages open knowledge sharing inside teams."),
            para_str("Another closing narrative paragraph continues normal first-line indentation in the column. " * 2),
        ]
        laporan = {"masalah": [], "peringatan": [], "normalisasi": []}
        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-hyp-pdf-") as td:
            td_p = Path(td)
            rel_td = td_p.relative_to(BASE).as_posix()
            out_blocks, _ = engine.ubah_isi(blocks, td, laporan, di_tempat=True)
            ast_doc = {"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": out_blocks}
            proc = subprocess.run(
                [engine.PANDOC, "-f", "json", "-t", "typst", "--wrap=none"],
                input=json.dumps(ast_doc).encode("utf-8"),
                capture_output=True,
                check=True,
            )
            body_typ_text = '#import "/template_jurnal.typ": *\n\n' + proc.stdout.decode("utf-8")
            body_typ = td_p / "body.typ"
            main_typ = td_p / "main.typ"
            meta_json = td_p / "meta.json"
            pdf_out = td_p / "out.pdf"
            body_typ.write_text(body_typ_text, encoding="utf-8")
            meta_json.write_text(
                json.dumps({
                    "judul": "Hypothesis Layout Verification",
                    "penulis": [{"nama": "Author One", "afiliasi": "1", "korespondensi": True}],
                    "afiliasi": ["Inst"],
                    "email": "a@example.com",
                    "riwayat": {},
                    "abstrak": "Abstrak pengujian tata letak hipotesis.",
                    "kata_kunci": ["hypothesis", "layout", "grid"],
                    "sitasi_penulis": "Author, A.",
                    "judul_kalimat": "Hypothesis layout verification",
                    "header_ganjil": "Author",
                    "terbitan": {"volume": "6", "nomor": "1", "edisi": "March", "tahun": "2026", "halaman_awal": 1},
                }),
                encoding="utf-8",
            )
            main_typ.write_text(
                '#import "/template_jurnal.typ": *\n'
                '#let cfg = json("/jurnal/bsscd/config.json")\n'
                f'#let meta = json("/{rel_td}/{meta_json.name}")\n'
                '#show: artikel.with(cfg, meta)\n'
                f'#include "/{rel_td}/{body_typ.name}"\n',
                encoding="utf-8",
            )
            subprocess.run(
                [engine.TYPST, "compile", "--root", str(BASE), "--font-path", str(BASE / "fonts"), str(main_typ), str(pdf_out)],
                check=True,
                capture_output=True,
            )
            pos, _ = engine.kueri_posisi(str(main_typ))
            self.assertEqual(engine.baris_luar_grid(str(pdf_out), pos, body_typ_text), [])

            # Pastikan editor_blok mengekstrak blok hipotesis tanpa error
            parsed_blocks = editor_blok.parse_body(body_typ_text)
            labeled = [b for b in parsed_blocks if b.get("jenis") == "labeled_paragraph"]
            self.assertGreaterEqual(len(labeled), 3)

            all_lines = []
            with fitz.open(str(pdf_out)) as doc:
                for pg in doc:
                    for blk in pg.get_text("dict").get("blocks", []):
                        for ln in blk.get("lines", []):
                            txt = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
                            if txt and round(float(ln["spans"][0]["size"]), 1) == 11.0 and ln["spans"][0]["origin"][1] > 380.0:
                                col = 0 if ln["bbox"][0] < 295.0 else 1
                                all_lines.append((
                                    col,
                                    float(ln["bbox"][0]),
                                    float(ln["spans"][0]["origin"][1]),
                                    txt,
                                ))
            all_lines.sort(key=lambda item: (item[0], item[2]))

            def find_line(prefix):
                for idx, item in enumerate(all_lines):
                    if item[3].startswith(prefix):
                        return idx, item
                self.fail(f"Rendered line starting with {prefix!r} not found")

            for prefix in ("H1:", "H6:", "H7:", "Hypothesis 8:"):
                idx, cur = find_line(prefix)
                col_x = 76.535 if cur[0] == 0 else 305.135
                self.assertAlmostEqual(cur[1], col_x, delta=1.5, msg=f"{prefix} unexpected indent x0={cur[1]}")
                if idx > 0 and all_lines[idx - 1][0] == cur[0]:
                    dy = cur[2] - all_lines[idx - 1][2]
                    self.assertAlmostEqual(dy, 22.0, delta=0.4, msg=f"{prefix} expected 11pt blank gap (dy=22pt), got {dy:.2f}")

            _, norm_p = find_line("Another")
            col_x_norm = 76.535 if norm_p[0] == 0 else 305.135
            self.assertAlmostEqual(norm_p[1], col_x_norm + 25.0, delta=1.5)

    def test_susun_teks_merges_consecutive_floating_objects_without_splitting_columns(self):
        # Memastikan dua objek apung berurutan (atas + bawah) menggabungkan batas #kolom[...]
        # tanpa membelah paragraf #pm(62) ke dua segmen kolom terpisah dan melompat ke kolom kanan.
        p60 = (
            "Common method bias was assessed using the full collinearity approach proposed earlier. "
            "All variance inflation factor values remained well below the conservative threshold across constructs. "
        ) * 3
        p62 = (
            "Direct effects were tested through bootstrapping, with results and bias-corrected "
            "confidence intervals reported in Table 6. All five direct relationships are positive and "
            "statistically significant, with t-statistics above 1.96 and confidence intervals excluding zero. "
            "H1 through H5 are supported."
        )
        p63 = (
            "Both indirect effects are positive and significant. Business innovation mediates the effect of "
            "technological absorptive capacity on business performance and entrepreneurial marketing on "
            "business performance. "
        ) * 5

        with tempfile.TemporaryDirectory(dir=BASE, prefix="test-consec-float-") as td:
            td_p = Path(td)
            img_path = td_p / "fig.png"
            from PIL import Image
            Image.new("RGB", (800, 300), (220, 225, 235)).save(img_path)
            rel_img = "/" + img_path.relative_to(BASE).as_posix()

            body_dasar = (
                "#kolom[\n"
                f"#pm(60);{p60.strip()}#pe(60)\n\n"
                "#celah()\n"
                "== #pm(61);Hypothesis Testing#pe(61)\n"
                f"#pm(62);{p62}#pe(62)\n\n"
                "]\n"
                '#tabel(jumlah-kolom: 2, penuh: true, kepala: 1, di-tempat: true, keputusan: "@K7@", label: [Table 6.], isi: [Direct effects])[\n'
                "#table(\n"
                "  columns: (120pt, 120pt),\n"
                "  table.header(table.cell(x: 0, y: 0)[Path], table.cell(x: 1, y: 0)[Result]),\n"
                "  table.cell(x: 0, y: 1)[H1], table.cell(x: 1, y: 1)[Supported],\n"
                ")\n"
                "]\n"
                "#kolom[\n"
                "]\n"
                f'#gambar("{rel_img}", lebar-word: 425.2pt, di-tempat: true, keputusan: "@K8@", label: [Figure 1.], isi: [Structural model])\n'
                "#kolom[\n"
                f"#pm(63);{p63.strip()}#pe(63)\n"
                "]\n"
            )

            merged = engine.susun_teks(body_dasar, {7: {"jenis": "atas"}, 8: {"jenis": "bawah"}}, akhir_seimbang=True)
            self.assertEqual(merged.count("#kolom["), 1, merged)
            self.assertIn('keputusan: "atas", nomor: 7', merged)
            self.assertIn('keputusan: "bawah", nomor: 8', merged)
            self.assertNotIn("]\n#tabel(", merged)

            mixed1 = engine.susun_teks(body_dasar, {7: {"jenis": "atas"}, 8: {"jenis": "tempat"}}, akhir_seimbang=True)
            self.assertEqual(mixed1.count("#kolom["), 2, mixed1)
            mixed2 = engine.susun_teks(body_dasar, {7: {"jenis": "tempat"}, 8: {"jenis": "bawah"}}, akhir_seimbang=True)
            self.assertEqual(mixed2.count("#kolom["), 2, mixed2)

            body_typ = td_p / "body.typ"
            meta_json = td_p / "meta.json"
            main_typ = td_p / "main.typ"
            pdf_out = td_p / "out.pdf"
            body_typ.write_text('#import "/template_jurnal.typ": *\n' + merged, encoding="utf-8")
            meta_json.write_text(
                json.dumps({
                    "judul": "Consecutive Float Merge Test",
                    "penulis": [{"nama": "Author One", "afiliasi": "1", "korespondensi": True}],
                    "afiliasi": ["Inst"],
                    "email": "a@example.com",
                    "riwayat": {},
                    "abstrak": "Abstract text.",
                    "kata_kunci": ["a", "b"],
                    "sitasi_penulis": "Author, A.",
                    "judul_kalimat": "Consecutive float merge test",
                    "header_ganjil": "Author",
                    "terbitan": {"volume": "5", "nomor": "4", "edisi": "December", "tahun": "2026", "halaman_awal": 726},
                }),
                encoding="utf-8",
            )
            rel_td = td_p.relative_to(BASE).as_posix()
            main_typ.write_text(
                '#import "/template_jurnal.typ": *\n'
                '#let cfg = json("/jurnal/bsscd/config.json")\n'
                f'#let meta = json("/{rel_td}/{meta_json.name}")\n'
                '#show: artikel.with(cfg, meta)\n'
                f'#include "/{rel_td}/{body_typ.name}"\n',
                encoding="utf-8",
            )
            subprocess.run(
                [engine.TYPST, "compile", "--root", str(BASE), "--font-path", str(BASE / "fonts"), str(main_typ), str(pdf_out)],
                check=True,
                capture_output=True,
            )

            narasi_urut = []
            with fitz.open(str(pdf_out)) as doc:
                for pg_idx, pg in enumerate(doc):
                    col_lines = {0: [], 1: []}
                    for blk in pg.get_text("dict").get("blocks", []):
                        for ln in blk.get("lines", []):
                            txt = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
                            if not txt or not ln.get("spans"):
                                continue
                            if abs(float(ln["spans"][0]["size"]) - 11.0) > 0.2:
                                continue
                            y = float(ln["spans"][0]["origin"][1])
                            if y <= 102.4 or (pg_idx == 0 and y < 380.0):
                                continue
                            col = 0 if ln["bbox"][0] < 295.0 else 1
                            col_lines[col].append((y, txt))
                    for col in (0, 1):
                        col_lines[col].sort(key=lambda item: item[0])
                        narasi_urut.extend(t for _, t in col_lines[col])

            joined = " ".join(narasi_urut)
            idx_direct = joined.find("Direct effects were tested")
            idx_conf = joined.find("confidence intervals reported in Table 6")
            idx_h1h5 = joined.find("H1 through H5 are supported")
            idx_indirect = joined.find("Both indirect effects are positive and significant")
            self.assertNotIn(-1, (idx_direct, idx_conf, idx_h1h5, idx_indirect), joined)
            self.assertLess(idx_direct, idx_conf)
            self.assertLess(idx_conf, idx_h1h5)
            self.assertLess(idx_h1h5, idx_indirect)

    def test_pindah_pita_atas_tabel_belah_par_preserves_reading_order_and_guards_bounds(self):
        body_A = (
            "#kolom[\n"
            "#pm(1);Direct effects were tested through bootstrapping, with results and bias-corrected "
            "confidence intervals reported in Table 6. All five direct relationships are positive and "
            "H1 through H5 are supported.#pe(1)\n\n"
            "#pm(2);Both indirect effects are positive and significant.#pe(2)\n\n"
            "]\n"
            '#tabel(keputusan: "@K1@", penuh: true)[\n'
            "  #table(columns: 2, [A], [B])\n"
            "]\n"
            "#kolom[\n"
            "#pm(3);Paragraph after table.#pe(3)\n"
            "]\n"
        )
        res_A = engine.pindah_pita_atas_tabel(
            body_A, 1, "belah_par", 1, "confidence intervals reported in Table 6"
        )
        i_bag1 = res_A.find("Direct effects were tested")
        i_tbl = res_A.find("#tabel(")
        i_bag2 = res_A.find("#lanjutan[#pf(1, 1);confidence intervals reported in Table 6")
        i_pm2 = res_A.find("#pm(2);Both indirect effects")
        i_pm3 = res_A.find("#pm(3);Paragraph after table.")
        self.assertNotIn(-1, (i_bag1, i_tbl, i_bag2, i_pm2, i_pm3), res_A)
        self.assertLess(i_bag1, i_tbl)
        self.assertLess(i_tbl, i_bag2)
        self.assertLess(i_bag2, i_pm2)
        self.assertLess(i_pm2, i_pm3)

        # Kasus B: paragraf #pm(3) berada SESUDAH Tabel 1 -> tidak boleh dipindah ke sebelum #pm(3)
        res_B = engine.pindah_pita_atas_tabel(
            body_A.replace("#pm(3);Paragraph after table.#pe(3)", "#pm(3);Paragraph after table continues nicely across words.#pe(3)"),
            1,
            "belah_par",
            3,
            "after table continues nicely",
        )
        self.assertEqual(
            res_B,
            body_A.replace("#pm(3);Paragraph after table.#pe(3)", "#pm(3);Paragraph after table continues nicely across words.#pe(3)"),
        )

    def test_belah_paragraf_rejects_paragraph_already_modified_by_tarik_kata(self):
        p1 = ("First paragraph sentence with sufficient words for test coverage here. " * 4).strip()
        p2 = ("Second paragraph follows with plenty of narrative words so subtracting a few words never creates a widow anywhere. " * 3).strip()
        body_raw = f"#kolom[\n#pm(1);{p1}#pe(1)\n\n#pm(2);{p2}#pe(2)\n]\n"
        stolen = engine.tarik_kata_paragraf(body_raw, 1, 2, 4)
        self.assertIsNotNone(stolen)
        self.assertIsNone(engine.potong_paragraf(stolen, 1, "words for test coverage"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
