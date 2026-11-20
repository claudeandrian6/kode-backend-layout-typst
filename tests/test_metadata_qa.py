"""Bounded final-page QA and metadata-only editing contract."""
import copy
import tempfile
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
def load(name, file):
    s=importlib.util.spec_from_file_location(name,BASE/file)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
build=load("new_build","build.py")
editor=load("new_editor","editor_blok.py")

class FinalPageTest(unittest.TestCase):
    def row(self, gap, col=1, page=3):
        return {"hal":page,"kol":col,"kurang":gap}

    def test_final_right_200_passes(self):
        self.assertTrue(build.kolom_sah(self.row(200),3))

    def test_final_right_over_200_fails(self):
        self.assertFalse(build.kolom_sah(self.row(201),3))

    def test_final_left_is_still_strict(self):
        self.assertFalse(build.kolom_sah(self.row(11,col=0),3))

    def test_middle_reference_page_does_not_get_exception(self):
        text="#referensi[#pm(10);First#pe(10)]\n#referensi[#pm(11);Last#pe(11)]"
        pos={"frag-akhir-10-0":([10,0],2,305,749.2),
             "frag-akhir-11-0":([11,0],3,305,749.2)}
        rows=[self.row(33,page=2),self.row(33,page=3)]
        build.tandai_batas_akhir(rows,pos,text,3)
        self.assertFalse(build.kolom_sah(rows[0],3))
        self.assertTrue(build.kolom_sah(rows[1],3))

    def test_reference_end_before_appendix_must_be_full(self):
        text="#referensi[#pm(10);Last#pe(10)]\n= #pm(11);APPENDIX#pe(11)"
        pos={"frag-akhir-10-0":([10,0],2,305,650)}
        rows=[self.row(199,page=2),self.row(201,page=2)]
        build.tandai_batas_akhir(rows,pos,text,3)
        self.assertFalse(build.kolom_sah(rows[0],3))
        self.assertFalse(build.kolom_sah(rows[1],3))

class MetadataTest(unittest.TestCase):
    def meta(self):
        return {"judul":"Original TITLE","judul_kalimat":"Preserve this derived title",
                "header_ganjil":"Preserve header","sitasi_penulis":"Preserve citation",
                "penulis":[{"nama":"Jane Smith","afiliasi":"1","korespondensi":True}],
                "afiliasi":["Institute"],"afiliasi_nomor":["1"],"abstrak":"Abstract",
                "kata_kunci":["One","Two","Three"],"email":"author@example.com",
                "riwayat":{"diterima":"30 August 2026","disetujui":"15 October 2026","terbit":"6 November 2026"},
                "terbitan":{"volume":5,"nomor":4,"tahun":2026,"halaman_awal":576,"edisi":"December"},
                "_artikel_id":"549-3461-1-LE"}

    def test_received_changes_only_received(self):
        meta=self.meta(); doc={"blocks":editor._blok_meta(meta)}
        next(b for b in doc["blocks"] if b["meta_key"]=="riwayat_diterima")["text"]="31 August 2026"
        result=editor.terapkan_meta(doc,copy.deepcopy(meta),{"riwayat_diterima"})
        expected=copy.deepcopy(meta);expected["riwayat"]["diterima"]="31 August 2026"
        self.assertEqual(result,expected)

    def test_title_formatter_is_the_production_formatter(self):
        self.assertEqual(editor._sentence_case("A New Approach: A Study"),build.sentence_case("A New Approach: A Study"))
        self.assertEqual(editor._sapaan_penulis(["Jane Smith","John Doe"]),build.apa_penulis(["Jane Smith","John Doe"]))

    def test_only_metadata_edits_use_the_safe_path(self):
        doc={"blocks":[{"id":"m-judul","jenis":"meta"},{"id":"p-1","jenis":"paragraph"}]}
        self.assertTrue(editor.edit_metadata_saja([{"block_id":"m-judul"}],[],[],doc))
        self.assertFalse(editor.edit_metadata_saja([{"block_id":"p-1"}],[],[],doc))
        self.assertFalse(editor.edit_metadata_saja([{"block_id":"m-judul"}],[{"move":"x"}],[],doc))

    def test_metadata_save_never_calls_body_renderer(self):
        meta=self.meta();doc={"blocks":editor._blok_meta(meta)}
        body='#import "/template_jurnal.typ": *\n#kaki-naik(6, 22.0pt)\n#pm(1);Keep every byte.#pe(1)\n'
        def read_json(path,*a):
            return copy.deepcopy(meta) if str(path).endswith("meta.json") else {}
        with patch.object(editor,"muat_dokumen",return_value=doc), \
             patch.object(editor,"baca_teks",return_value=body), \
             patch.object(editor,"baca_json",side_effect=read_json), \
             patch.object(editor,"_simpan_snapshot"),patch.object(editor,"simpan_dokumen"), \
             patch.object(editor,"_terapkan_dan_kompilasi",return_value={"ok":True}) as compile_, \
             patch.object(editor,"render_body",side_effect=AssertionError("Body renderer called")), \
             patch.object(editor,"ruang_kerja",return_value={"ok":True,"halaman":23}), \
             patch.object(editor,"_halaman_berubah",return_value=[]):
            result=editor.aksi_save("/tmp/metadata-contract",{"edits":[
                {"block_id":"m-riwayat-diterima","text":"31 August 2026"}]})
        self.assertTrue(result["ok"])
        self.assertEqual(compile_.call_args.args[2],body)
        self.assertEqual(compile_.call_args.args[3]["riwayat"]["diterima"],"31 August 2026")

class MetadataQaCacheTest(unittest.TestCase):
    def test_metadata_change_invalidates_qa_without_changing_body(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/"body.typ").write_text("Same body")
            (folder/"meta.json").write_text('{"received":"old"}')
            cached={"versi":3,"sumber_hash":editor.hash_sumber_qa(directory),
                    "qa":{"status":"LULUS"}}
            editor.tulis_json(str(folder/editor.FOLDER_QA),cached)
            self.assertEqual(editor._qa_terbaru(directory),cached["qa"])
            (folder/"meta.json").write_text('{"received":"new"}')
            self.assertIsNone(editor._qa_terbaru(directory))
            self.assertEqual((folder/"body.typ").read_text(),"Same body")

    def test_template_change_invalidates_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/"template_jurnal.typ").write_text("Original template")
            with patch.object(editor,"BASE",directory):
                cached={"versi":3,"sumber_hash":editor.hash_sumber_qa(directory),
                        "qa":{"status":"LULUS"}}
                editor.tulis_json(str(folder/editor.FOLDER_QA),cached)
                self.assertEqual(editor._qa_terbaru(directory),cached["qa"])
                (folder/"template_jurnal.typ").write_text("Changed template")
                self.assertIsNone(editor._qa_terbaru(directory))

    def test_changed_metadata_during_qa_does_not_publish_stale_result(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/"meta.json").write_text('{"received":"old"}')
            def compile_(*args,**kwargs):
                (folder/"meta.json").write_text('{"received":"new"}')
                return type("Completed",(),{"stdout":b'{"ok":true,"masalah":[]}',
                                           "stderr":b""})()
            with patch.object(editor.subprocess,"run",side_effect=compile_):
                result=editor.jalankan_qa(directory)
            self.assertTrue(result["basi"])
            self.assertFalse((folder/editor.FOLDER_QA).exists())

if __name__=="__main__":
    unittest.main(verbosity=2)
