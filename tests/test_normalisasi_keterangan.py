"""Caption table/figure normalization in normalisasi.php (label bold, text regular sentence case)."""
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parents[1]
if not (BASE / "normalisasi.php").exists():
    BASE = Path("/opt/typst_editor")

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % NS_W

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
 <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="%s"><w:docDefaults><w:rPrDefault><w:rPr/></w:rPrDefault></w:docDefaults></w:styles>""" % NS_W

DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="%s"><w:body>
 <w:p><w:r><w:t>INTRODUCTION</w:t></w:r></w:p>
 <w:p><w:r><w:rPr><w:b/><w:i/><w:sz w:val="20"/></w:rPr><w:t>Table 1. Thematic Coding Framework</w:t></w:r></w:p>
 <w:p><w:r><w:t>Table 4 shows the distribution of the coding framework in the manuscript.</w:t></w:r></w:p>
 <w:p><w:r><w:t>Figure 1. THEMATIC MAP OF INTEGRATED SFL EXCHANGE ROLES</w:t></w:r></w:p>
 <w:p><w:r><w:t>CONCLUSION</w:t></w:r></w:p>
 <w:sectPr/>
</w:body></w:document>""" % NS_W


class CaptionNormalizationTest(unittest.TestCase):
    def setUp(self):
        if not shutil.which("php"):
            self.skipTest("PHP tidak tersedia")

    def make_docx(self, folder):
        docx = folder / "caption.docx"
        with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", CONTENT_TYPES)
            z.writestr("_rels/.rels", RELS)
            z.writestr("word/_rels/document.xml.rels", DOC_RELS)
            z.writestr("word/styles.xml", STYLES)
            z.writestr("word/document.xml", DOCUMENT)
        return docx

    def test_caption_label_bold_and_text_sentence_case(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            docx = self.make_docx(folder)
            out = folder / "out.docx"
            report = folder / "report.json"
            run = subprocess.run(
                ["php", str(BASE / "normalisasi.php"), str(docx), str(out),
                 "--laporan", str(report), "--diam"],
                capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as z:
                root = ET.fromstring(z.read("word/document.xml"))
            paras = ["".join(t.text or "" for t in p.iter(W + "t"))
                     for p in root.iter(W + "p")]
            captions = [p for p in paras if p.startswith(("Table 1.", "Figure 1."))]
            self.assertIn("Table 1. Thematic coding framework", captions)
            self.assertIn("Figure 1. Thematic map of integrated SFL exchange roles", captions)
            # Narasi "Table 4 shows ..." tanpa titik setelah nomor tidak boleh diubah.
            self.assertTrue(any(p.startswith("Table 4 shows the distribution") for p in paras))
            # Run pertama label tebal/non-miring; run kedua isi regular/non-miring.
            cap = next(p for p in root.iter(W + "p")
                       if "".join(t.text or "" for t in p.iter(W + "t"))
                       .startswith("Table 1."))
            runs = [r for r in cap.findall(W + "r")
                    if "".join(t.text or "" for t in r.iter(W + "t"))]
            self.assertEqual("".join(t.text or "" for t in runs[0].iter(W + "t")), "Table 1. ")
            self.assertIsNotNone(runs[0].find(W + "rPr/" + W + "b"))
            self.assertEqual(runs[0].find(W + "rPr/" + W + "i").get(W + "val"), "0")
            self.assertEqual("".join(t.text or "" for t in runs[1].iter(W + "t")),
                             "Thematic coding framework")
            self.assertEqual(runs[1].find(W + "rPr/" + W + "b").get(W + "val"), "0")
            self.assertEqual(runs[1].find(W + "rPr/" + W + "i").get(W + "val"), "0")


if __name__ == "__main__":
    unittest.main()
