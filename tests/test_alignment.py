"""Regression checks for fixed geometry and paragraph identity on real Typst PDFs."""
import importlib.util
import tempfile
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("layout_build", BASE / "build.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
engine.TYPST = "/usr/local/bin/typst"

PRELUDE = """#import "/template_jurnal.typ": *
#set page(width: 595.27pt, height: 841.89pt,
 margin: (left: 76.535pt, right: 76.535pt, top: 102.4pt, bottom: 79pt), columns: 2)
#set text(font: "Times New Roman", size: 11pt, top-edge: 0.8em, bottom-edge: -0.2em)
#set par(leading: 0pt, spacing: 0pt, first-line-indent: 0pt)
"""

def paragraph(n, lines, part=None):
    first = "#pm(%d);" % n if part is None else "#pf(%d, %d);" % (n, part)
    last = "#pe(%d)" % n if part is None else "#pe(%d, bagian: %d)" % (n, part)
    return first + "#linebreak()".join("Sample line %d" % i for i in range(lines)) + last

class AlignmentTest(unittest.TestCase):
    def measure(self, text):
        with tempfile.TemporaryDirectory(prefix="alignment-test-", dir=BASE) as directory:
            main = Path(directory) / "main.typ"
            pdf = Path(directory) / "test.pdf"
            main.write_text(PRELUDE + text)
            rc, _, err = engine.jalankan([engine.TYPST, "compile", "--root", str(BASE),
                "--font-path", str(BASE / "fonts"), str(main), str(pdf)])
            self.assertEqual(rc, 0, err)
            pos, err = engine.kueri_posisi(str(main))
            self.assertIsNotNone(pos, err)
            return engine.ukur_layout_ketat(str(pdf), pos, text)

    def test_exact_columns(self):
        u = self.measure(paragraph(1, 60) + "\n#colbreak()\n" + paragraph(2, 60))
        self.assertTrue(u["lulus"], u)
        self.assertTrue(all(abs(x["kurang"]) <= engine.ALIGN_EPS for x in u["kolom"]))

    def test_both_short_is_rejected(self):
        u = self.measure(paragraph(1, 59) + "\n#colbreak()\n" + paragraph(2, 59))
        self.assertFalse(u["lulus"])
        self.assertAlmostEqual(u["kolom"][0]["kurang"], 11, places=2)

    def test_last_right_may_be_short(self):
        u = self.measure(paragraph(1, 60) + "\n#colbreak()\n" + paragraph(2, 43))
        self.assertTrue(u["lulus"], u)

    def test_manual_split_identity_is_checked(self):
        text = (paragraph(1, 59) + "\n\n" + paragraph(2, 1)
                + "\n#colbreak()\n" + paragraph(2, 2, 1) + "\n\n" + paragraph(3, 58))
        u = self.measure(text)
        self.assertFalse(u["lulus"])
        self.assertTrue(any(t.get("pm") == 2 for t in u["tunggal"]), u)

    def test_fragment_checker_works_without_legacy_heuristic(self):
        from unittest.mock import patch
        text = (paragraph(1, 59) + "\n\n" + paragraph(2, 1)
                + "\n#colbreak()\n" + paragraph(2, 2, 1) + "\n\n" + paragraph(3, 58))
        with patch.object(engine, "baris_tunggal", return_value=[]):
            u = self.measure(text)
        self.assertFalse(u["lulus"])
        self.assertTrue(any(t.get("pm") == 2 for t in u["tunggal"]), u)

    def test_last_left_must_be_full(self):
        u = self.measure(paragraph(1, 59) + "\n#colbreak()\n" + paragraph(2, 20))
        self.assertFalse(u["lulus"])

    def test_single_column_object_does_not_mask_other_column(self):
        pos = {"objek-info-1": ((1, 100), 1, 305, 200),
               "objek-akhir-1": ("akhir", 2, 76.5, 300)}
        text = '#gambar("sample.png", penuh: false, nomor: 1)'
        from unittest.mock import patch
        with patch.object(engine, "objek_penuh_teks", return_value=False):
            area = engine.area_objek_ketat(pos, text)
        self.assertNotIn((1, 0), area)
        self.assertIn((1, 1), area)
        self.assertIn((2, 0), area)
        self.assertNotIn((2, 1), area)

    def test_end_marker_does_not_count_next_heading_as_fragment(self):
        text = (paragraph(1, 60) + "\n#colbreak()\n"
                + "== #pm(2);Next heading#pe(2)\n\n" + paragraph(3, 20))
        u = self.measure(text)
        self.assertFalse(any(t.get("pm") == 1 for t in u["tunggal"]), u)

    def test_missing_end_marker_is_rejected(self):
        u = self.measure(paragraph(1, 60).replace("#pe(1)", "")
                         + "\n#colbreak()\n" + paragraph(2, 60))
        self.assertFalse(u["lulus"])
        self.assertTrue(any("penanda" in s.lower() for s in u["masalah"]))

    def test_nonterminal_right_may_hang_two_lines(self):
        text = (paragraph(1, 60) + "\n#colbreak()\n" + paragraph(2, 59)
                + "\n#pagebreak()\n" + paragraph(3, 60) + "\n#colbreak()\n" + paragraph(4, 42))
        u = self.measure(text)
        self.assertTrue(u["lulus"], u)
        self.assertTrue(any(x["hal"] == 1 and x["kol"] == 1
                            and abs(x["kurang"] - 11) < engine.ALIGN_EPS for x in u["kolom"]))


    def test_short_left_outranks_two_short_rights(self):
        def row(kol,kurang):
            return dict(hal=2,kol=kol,kurang=kurang,target=760.2,
                        akhir_baseline=749.2,baris_terlihat=50,kosong=False)
        left_bad=dict(halaman=3,masalah=["kiri"],grid=[],tunggal=[],
                      kolom=[row(0,11),row(1,0)])
        right_bad=dict(halaman=3,masalah=["kanan"],grid=[],tunggal=[],
                       kolom=[row(0,0),row(1,33)])
        self.assertLess(engine.nilai_kandidat_layout(right_bad,{},11),
                        engine.nilai_kandidat_layout(left_bad,{},11))
        self.assertLess(engine.kunci_layout_ketat(right_bad),
                        engine.kunci_layout_ketat(left_bad))


    def test_balanced_last_page_may_end_short(self):
        left=dict(hal=2,kol=0,kurang=396,target=760.2,akhir_baseline=364.2,
                  baris_terlihat=23,kosong=False,akhir_seimbang=True)
        right=dict(hal=2,kol=1,kurang=407,target=760.2,akhir_baseline=353.2,
                   baris_terlihat=24,kosong=False,akhir_seimbang=True)
        self.assertTrue(engine.kolom_sah(left,2))
        self.assertTrue(engine.kolom_sah(right,2))
        left["akhir_seimbang"]=False
        self.assertFalse(engine.kolom_sah(left,2))

    def test_balanced_final_segment_candidate_keeps_kolom(self):
        body="#kolom[\nIsi segmen.\n]\n"
        self.assertIn("#kolom-lanjut[",engine.susun_teks(body,{}))
        self.assertIn("#kolom[",engine.susun_teks(body,{},akhir_seimbang=True))
        self.assertNotIn("#kolom-lanjut[",engine.susun_teks(body,{},akhir_seimbang=True))

if __name__ == "__main__":
    unittest.main(verbosity=2)
