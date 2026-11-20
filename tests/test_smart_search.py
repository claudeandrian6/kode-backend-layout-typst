"""Search fairness, preserved cuts and recipes bound to content; every seed is measured."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("search_engine",BASE/"build.py")
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)


class SearchTest(unittest.TestCase):
    def test_operator_round_robin_prevents_cut_starvation(self):
        cuts=["cut"+str(i) for i in range(100)]
        sizes=["size1","size2"]
        self.assertEqual(engine.sela_kandidat([cuts,sizes,["header"],["place"]],4),
                         ["cut0","size1","header","place"])

    def test_object_resize_preserves_selected_column_break(self):
        body=('#kolom[\n#pm(1);First paragraph text.#pe(1)\n\n'
              '#pm(2);Second paragraph text.#pe(2)\n]\n'
              '#gambar("image.png", keputusan: "@K1@", nomor: 1)\n')
        for scale in (.8,.81):
            text=engine.render_kandidat_layout(body,{1:{"jenis":"tempat","skala":scale}},[("putus",2)])
            self.assertIn("#colbreak()\n\n#pm(2)",text)
            self.assertIn("Second paragraph text",text)
            self.assertIn("skala: %.3f"%scale,text)

    def test_recipe_is_invalidated_by_input_or_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            docx=Path(directory)/"article.docx";docx.write_bytes(b"source version one")
            meta={"title":"Original"}
            engine.simpan_resep_layout(str(docx),meta,{1:{"jenis":"tempat","skala":.8}},11,[])
            self.assertEqual(engine.muat_resep_layout(str(docx),meta)["header"],11)
            self.assertIsNone(engine.muat_resep_layout(str(docx),{"title":"Changed"}))
            docx.write_bytes(b"source version two")
            self.assertIsNone(engine.muat_resep_layout(str(docx),meta))

    def test_invalid_header_cannot_enter_template(self):
        with tempfile.TemporaryDirectory() as directory:
            main=Path(directory)/"main.typ";main.write_text("#show: artikel.with(cfg, meta)\n")
            with self.assertRaises(ValueError):
                engine.atur_header_grid(str(main),5.5)
            self.assertEqual(main.read_text(),"#show: artikel.with(cfg, meta)\n")

    def test_recipe_cannot_bypass_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);(folder/"main.typ").write_text("#show: artikel.with(cfg, meta)\n")
            u={"halaman":1,"kolom":[],"grid":[],"tunggal":[],"masalah":["held by QA"],"lulus":False}
            stat={"resep_layout":{"keputusan":{},"header":11,"potongan":[]}}
            with patch.dict(engine.os.environ,{"ALIGN_MAX_EVAL":"1"}), \
                 patch.object(engine,"nilai_tata_letak",return_value=(0,{"ketat":u},{})) as measure, \
                 patch.object(engine,"qa_tata_letak",return_value=([],[],[])), \
                 patch.object(engine,"pita_kecil",return_value=[]), \
                 patch.object(engine,"spesifikasi_potong_layout",return_value=[]):
                engine.pilih_kandidat("Source",str(folder/"body.typ"),directory,{},[],{},[],stat)
            self.assertEqual(measure.call_count,1)
            self.assertFalse(stat["alignment_pencarian"]["lulus"])
            self.assertEqual(stat["alignment_pencarian"]["operator"],{"resep":1})


if __name__=="__main__":
    unittest.main()
