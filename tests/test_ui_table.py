"""Regression checks for the Web QA contract, Pandoc table wrappers and scoped footer permits."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import pymupdf as fitz

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("layout_engine", BASE / "build.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

class IntegrationContractTest(unittest.TestCase):
    def test_web_qa_uses_existing_build_functions(self):
        tree = ast.parse((BASE / "qa_cepat.py").read_text())
        names = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "build"}
        for name in names:
            self.assertTrue(callable(getattr(engine, name, None)), name)

    def test_pandoc_31_wrapper_without_kind(self):
        table = "#table(\ncolumns: 2,\n[Name], [Value], [Alpha], [1],\n)"
        text = "#figure(\nalign(center)[" + table + "]\n)"
        actual, count = engine.buka_figure_tabel(text)
        self.assertEqual(actual, table)
        self.assertEqual(count, 1)

    def test_pandoc_wrapper_with_kind(self):
        table = "#table(columns: 1, [Name])"
        actual, count = engine.buka_figure_tabel("#figure(align(center)[" + table + "], kind: table)")
        self.assertEqual((actual, count), (table, 1))

    def test_unknown_caption_is_not_discarded(self):
        text = "#figure(align(center)[#table(columns: 1, [Name])], caption: [Keep me])"
        self.assertEqual(engine.buka_figure_tabel(text), (text, 0))

    def test_table_overrides_removed_but_content_preserved(self):
        text = "#table(\ncolumns: 2,\n  align: (col, row) => (auto,auto,).at(col),\n  inset: 6pt,\n[Name], [Value],\n)"
        actual = engine.gaya_tabel_typst(text)
        self.assertNotIn("align:", actual)
        self.assertNotIn("inset:", actual)
        self.assertIn("[Name], [Value]", actual)

    def test_cut_after_word_literal_parenthesis(self):
        text = r"#pm(105);Development of Standard Operating Procedures \(SOPs) together with BGN, management of food waste and emergency response.#pe(105)"
        self.assertIsNotNone(engine._belah_paragraf(text, 105, "management of food waste"))
        result = engine.potong_paragraf(text, 105, "management of food waste")
        self.assertIn("#pf(105, 1)", result)
        self.assertIn("management of food waste", result)

    def test_cannot_cut_inside_function_argument(self):
        text = '#pm(1);More information #link("https://example.org/hiddenargument")[visible text] here.#pe(1)'
        self.assertIsNone(engine._belah_paragraf(text, 1, "example.org/hiddenargument"))

    def test_reference_cut_does_not_need_global_reference_hint(self):
        from unittest.mock import patch
        def line(y, text, x):
            return {"kol":0 if x < 300 else 1, "bbox": (x,y-8.8,x+200,y+2.2), "spans": [
                {"size":11, "font":"TimesNewRoman", "origin":(x,y), "text":text}]}
        pages = {2:[{"lines":[line(738.2,"Beal and colleagues. ",305),
                              line(749.2,"A review of ",305),
                              line(760.2,"child stunting determinants in Indonesia.",305)]}],
                 3:[{"lines":[line(111.2,"continued reference text",76.5),
                              line(122.2,"continued DOI",76.5)]}]}
        pos={"pm-10":(10,2,305,738.2),"pm-11":(11,3,76.5,144.2)}
        text="#referensi[#pm(10);Beal and colleagues. A review of child stunting determinants in Indonesia. continued reference text continued DOI#pe(10)]"
        with patch.object(engine,"baris_narasi_terlihat",side_effect=lambda pdf,h:[l for b in pages.get(h,[]) for l in b["lines"]]), \
             patch.object(engine,"_jumlah_halaman",return_value=3):
            result=engine.cari_potong("unused",pos,2,1,1,text)
        self.assertIsNotNone(result)

    def footer(self, article=engine.TARGET_ARTIKEL, start=580):
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            directory = Path(directory)
            (directory/"meta.json").write_text(json.dumps(
                {"_artikel_id": article, "terbitan": {"halaman_awal": start}}))
            pdf = directory/"test.pdf"
            doc=fitz.open()
            doc.new_page(width=595.27, height=841.89)
            p=doc.new_page(width=595.27, height=841.89)
            p.draw_line((76.535,743.5),(518.735,743.5))
            doc.save(pdf)
            doc.close()
            return engine.footer_target(str(pdf), "#kaki-naik(2, 22.0pt)\n")

    def test_footer_permit_is_scoped_to_article(self):
        permitted, errors = self.footer(article="different-article")
        self.assertEqual(permitted, {})
        self.assertTrue(errors)

    def test_footer_permit_is_scoped_to_printed_page(self):
        permitted, errors = self.footer(start=581)
        self.assertEqual(permitted, {})
        self.assertTrue(errors)

    def test_footer_permit_requires_actual_footer(self):
        permitted, errors = self.footer()
        self.assertEqual(permitted, {2:22.0})
        self.assertEqual(errors, [])

    def test_equal_columns_exception_retains_physical_gap(self):
        rows = [dict(hal=6,kol=k,kurang=22.0,kosong=False,target=760.2,akhir_baseline=738.2,baris_terlihat=58) for k in (0,1)]
        engine.tandai_pengecualian_target(rows,{6:22.0})
        self.assertTrue(all(r["pengecualian_target"] for r in rows))
        self.assertTrue(all(r["kurang"] == 22.0 for r in rows))

    def test_unequal_columns_cannot_get_footer_exception(self):
        rows=[dict(hal=6,kol=0,kurang=22.0,kosong=False,target=760.2,akhir_baseline=738.2,baris_terlihat=58),
              dict(hal=6,kol=1,kurang=11.0,kosong=False,target=760.2,akhir_baseline=749.2,baris_terlihat=59)]
        engine.tandai_pengecualian_target(rows,{6:22.0})
        self.assertFalse(any(r["pengecualian_target"] for r in rows))

if __name__ == "__main__":
    unittest.main(verbosity=2)
