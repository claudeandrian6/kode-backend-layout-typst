"""Visible glyph baselines and verified right-column cuts; no production article build."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pymupdf as fitz

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("visible_engine", BASE / "build.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
engine.TYPST = "/usr/local/bin/typst"


class VisiblePdfTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE, prefix="visible-baseline-test-")
        self.folder = Path(self.temp.name)
        (self.folder / "meta.json").write_text(json.dumps(
            {"_artikel_id":engine.TARGET_ARTIKEL, "terbitan":{"halaman_awal":580}}))
        self.pdf = str(self.folder / "test.pdf")
        self.pos = {"pm-1": (1, 1, 76.535, 102.4)}

    def tearDown(self):
        self.temp.cleanup()

    def make(self, left=738.2, right=760.2, hidden=False, transparent=False, whitespace=False):
        with fitz.open() as doc:
            doc.new_page(width=595.27, height=841.89)
            page = doc.new_page(width=595.27, height=841.89)
            for x, y in ((76.535,left),(305.135,right)):
                if y is not None:
                    page.insert_text((x,y),"Last visible body line", fontsize=11)
            if hidden:
                page.insert_text((76.535,760.2),"Invisible padding",fontsize=11,render_mode=3)
            if transparent:
                page.insert_text((76.535,760.2),"Transparent padding",fontsize=11,fill_opacity=0)
            if whitespace:
                page.insert_text((76.535,760.2),"          ",fontsize=11)
            page.draw_line((76.535,743.5),(518.735,743.5))
            page.insert_text((300,752.6),"Journal | 581",fontsize=10)
            doc.save(self.pdf)

    def rows(self, text=""):
        return [x for x in engine.ukur_kolom(self.pdf,self.pos,text) if x["hal"] == 2]

    def test_left_short_22_right_full_cannot_get_footer_permit(self):
        self.make()
        text="#kaki-naik(2, 22.0pt)\n"
        rows=self.rows(text)
        self.assertAlmostEqual(rows[0]["kurang"],22,places=2)
        self.assertAlmostEqual(rows[1]["kurang"],0,places=2)
        self.assertFalse(any(x["pengecualian_target"] for x in rows))
        u=engine.ukur_layout_ketat(self.pdf,self.pos,text)
        self.assertFalse(u["lulus"])
        self.assertTrue(any("selisih 22.00 pt" in x for x in u["masalah"]),u["masalah"])
        self.assertIn((2,1),engine.baris_luber(self.pdf))

    def test_invisible_render_mode_does_not_fill_left(self):
        self.make(hidden=True)
        self.assertAlmostEqual(self.rows()[0]["akhir_baseline"],738.2,places=2)

    def test_transparent_text_does_not_fill_left(self):
        self.make(transparent=True)
        self.assertAlmostEqual(self.rows()[0]["akhir_baseline"],738.2,places=2)

    def test_whitespace_does_not_fill_left(self):
        self.make(whitespace=True)
        self.assertAlmostEqual(self.rows()[0]["akhir_baseline"],738.2,places=2)

    def test_empty_wrapper_does_not_supply_or_hide_a_baseline(self):
        self.make()
        self.pos.update({"objek-info-1":((1,33),2,76.535,729.4),
                         "objek-akhir-1":("akhir",2,76.535,762.4)})
        self.assertAlmostEqual(self.rows()[0]["akhir_baseline"],738.2,places=2)

    def test_empty_left_cannot_be_approved_and_json_is_finite(self):
        self.make(left=None,hidden=True)
        row=self.rows("#kaki-naik(2, 22.0pt)\n")[0]
        self.assertIsNone(row["akhir_baseline"])
        self.assertFalse(engine.kolom_sah(row,2))
        self.assertTrue(row["kosong"])
        json.dumps(row,allow_nan=False)

    def test_full_width_object_to_page_bottom_fills_both_columns(self):
        self.make()
        self.pos.update({"objek-info-1":((1,22.0),2,76.535,740.0),
                         "objek-akhir-1":("akhir",2,76.535,762.4)})
        rows=self.rows("#tabel(jumlah-kolom: 4, penuh: true, nomor: 1)")
        self.assertTrue(all(x["penuh_objek"] for x in rows),rows)
        self.assertTrue(all(abs(x["kurang"]) <= engine.ALIGN_EPS for x in rows),rows)
        self.assertFalse(any(x["kosong"] for x in rows))

    def test_full_width_object_above_page_bottom_does_not_fill(self):
        self.make()
        self.pos.update({"objek-info-1":((1,22.0),2,76.535,700.0),
                         "objek-akhir-1":("akhir",2,76.535,720.0)})
        rows=self.rows("#tabel(jumlah-kolom: 4, penuh: true, nomor: 1)")
        self.assertFalse(any(x["penuh_objek"] for x in rows),rows)
        self.assertAlmostEqual(rows[0]["kurang"],22,places=2)

    def test_actual_equal_baselines_can_get_scoped_permit(self):
        self.make(right=738.2)
        rows=self.rows("#kaki-naik(2, 22.0pt)\n")
        self.assertTrue(all(x["pengecualian_target"] for x in rows))
        self.assertEqual(engine.baris_luber(self.pdf),[])

    def test_text_below_original_body_limit_is_not_dropped(self):
        self.make(right=782.2)
        self.assertAlmostEqual(self.rows()[1]["akhir_baseline"],782.2,places=2)
        self.assertIn((2,1),engine.baris_luber(self.pdf))

    def test_wide_left_span_is_classified_by_its_origin(self):
        with fitz.open() as doc:
            page=doc.new_page(width=595.27,height=841.89)
            page.insert_text((76.535,760.2),"Long visible line "*5,fontsize=11)
            page.insert_text((305.135,738.2),"Right body",fontsize=11)
            doc.save(self.pdf)
        rows=engine.ukur_kolom(self.pdf,self.pos,"")
        self.assertAlmostEqual(rows[0]["akhir_baseline"],760.2,places=2)
        self.assertAlmostEqual(rows[1]["akhir_baseline"],738.2,places=2)


class VerifiedCutTest(unittest.TestCase):
    def test_noop_cut_is_rejected_before_footer(self):
        before={"hal":1,"kol":1,"kurang":0,"akhir_baseline":760.2,"kosong":False}
        u={"kolom":[before]}
        text="#pm(2);Some source text.#pe(2)"
        with patch.object(engine,"cari_potong",return_value=("putus",2)), \
             patch.object(engine,"terapkan_potong",return_value="#colbreak()\n"+text):
            result=engine.potong_target_terverifikasi(text,"unused",{},u,1,1,22,
                lambda _: (u,{"pm-2":(2,2,76.535,102.4)}))
        self.assertIsNone(result)

    def test_real_colbreak_moves_two_right_lines_to_next_page(self):
        prelude="""#import "/template_jurnal.typ": *
#set page(width: 595.27pt, height: 841.89pt,
 margin: (left: 76.535pt, right: 76.535pt, top: 102.4pt, bottom: 79pt), columns: 2)
#set text(font: "Times New Roman", size: 11pt, top-edge: 0.8em, bottom-edge: -0.2em)
#set par(leading: 0pt, spacing: 0pt, first-line-indent: 0pt)
"""
        def paragraph(n,count):
            return "#pm(%d);" % n + "#linebreak()".join(
                "Paragraph %d sample line %d" % (n,i) for i in range(count))+"#pe(%d)"%n
        text=(paragraph(1,58)+"\n#colbreak()\n"+paragraph(2,60)+"\n\n"+
              paragraph(3,58)+"\n#colbreak()\n"+paragraph(4,45))
        with tempfile.TemporaryDirectory(dir=BASE,prefix="verified-cut-test-") as directory:
            main=Path(directory)/"main.typ";pdf=Path(directory)/"test.pdf"
            def compile_(body):
                main.write_text(prelude+body)
                rc,_,err=engine.jalankan([engine.TYPST,"compile","--root",str(BASE),
                    "--font-path",str(BASE/"fonts"),str(main),str(pdf)])
                self.assertEqual(rc,0,err)
                pos,err=engine.kueri_posisi(str(main))
                self.assertIsNotNone(pos,err)
                return engine.ukur_layout_ketat(str(pdf),pos,body),pos
            before,pos=compile_(text)
            result=engine.potong_target_terverifikasi(text,str(pdf),pos,before,1,1,22,compile_)
            self.assertIsNotNone(result)
            after_text,after,_,proof=result
            rows=[x for x in after["kolom"] if x["hal"]==1]
            self.assertTrue(engine.baseline_target_sama(rows,22),rows)
            self.assertEqual(proof["baris_dipindah"],2)
            self.assertEqual(proof["halaman_tujuan"],2)
            self.assertAlmostEqual(proof["baseline_sebelum"]-proof["baseline_sesudah"],22,places=2)
            self.assertIn("#colbreak()",after_text)
            self.assertFalse(after["tunggal"],after["tunggal"])
            self.assertFalse(after["grid"],after["grid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
