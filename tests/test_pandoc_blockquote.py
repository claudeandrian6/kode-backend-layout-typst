"""Indentasi Word tidak boleh menyembunyikan metadata atau gambar."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("build", Path(__file__).resolve().parents[1] / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def para(text):
    return {"t": "Para", "c": [{"t": "Str", "c": text}]}


class PandocBlockQuoteTest(unittest.TestCase):
    def test_front_matter_and_body_images(self):
        authors = {"t": "Para", "c": [{"t": "Str", "c": "Jane Smith"},
                                       {"t": "Superscript", "c": [{"t": "Str", "c": "1"}]}]}
        image = {"t": "Para", "c": [{"t": "Image", "c": [["", [], []], [], ["a.png", ""]]}]}
        quote = {"t": "BlockQuote", "c": [para("A participant said this.")]}
        blocks = [para("Article title"), {"t": "BlockQuote", "c": [authors,
                  para("1 Department of Education, University"), para("Corresponding email: jane@example.org")]},
                  para("Abstract: Study text."), para("Keywords: one; two; three"),
                  {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "INTRODUCTION"}]]},
                  quote, {"t": "BlockQuote", "c": [image, para("Figure 1. Caption")]}]
        blocks = build.buka_blok_pandoc(blocks)
        meta, start = build.urai_depan(blocks)
        self.assertEqual(meta["penulis"][0]["nama"], "Jane Smith")
        self.assertEqual(meta["email"], "jane@example.org")
        self.assertEqual(meta["abstrak"], "Study text.")
        self.assertEqual(meta["kata_kunci"], ["one", "two", "three"])
        self.assertEqual(blocks[start]["t"], "Header")
        self.assertIn(quote, blocks)
        self.assertIn(image, blocks)


if __name__ == "__main__":
    unittest.main()
