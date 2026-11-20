"""Regression tests for editor_blok.render_body() preserving untouched #lanjutan splits."""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import editor_blok


SAMPLE_BODY = """#import "/template_jurnal.typ": *

#kolom[
#pm(1);First paragraph stays short and gets frozen.

#pm(2);Untouched split paragraph part one#linebreak()

#colbreak()
#lanjutan[part two continues in next column.]

#pm(3);Following paragraph stays after the split.
]
"""


class FreezeRenderBodyTest(unittest.TestCase):
    def _parse_dok(self, body_text):
        parsed = editor_blok.parse_body(body_text)
        blocks = editor_blok.blok_dari_parse(parsed)
        return {"blocks": blocks, "dihapus_pm": [], "dihapus_objek": []}

    def test_render_body_preserves_untouched_lanjutan_when_other_block_frozen(self):
        dok = self._parse_dok(SAMPLE_BODY)
        p1 = next(b for b in dok["blocks"] if b["src"].get("pm") == 1)
        p1["style"]["frozen"] = True
        p1["style"]["freeze_x"] = 10.0

        rendered = editor_blok.render_body(SAMPLE_BODY, dok)
        self.assertIn("#beku(10pt, 0pt)[#pm(1);", rendered)
        self.assertIn("#colbreak()\n#lanjutan[part two continues in next column.]", rendered)
        self.assertIn("#pm(2);Untouched split paragraph part one#linebreak()", rendered)
        self.assertNotIn("#beku(0pt, 0pt)[#pm(2);", rendered)

    def test_render_body_rewrites_and_drops_lanjutan_when_split_paragraph_is_edited(self):
        dok = self._parse_dok(SAMPLE_BODY)
        p2 = next(b for b in dok["blocks"] if b["src"].get("pm") == 2)
        p2["style"]["frozen"] = True
        p2["style"]["freeze_x"] = 5.0

        rendered = editor_blok.render_body(SAMPLE_BODY, dok)
        self.assertIn("#beku(5pt, 0pt)[#pm(2);", rendered)
        self.assertNotIn("#lanjutan[", rendered)
        self.assertNotIn("#colbreak()", rendered)

    def test_freeze_box_width_slot_h_leading_tracking_roundtrip(self):
        dok = self._parse_dok(SAMPLE_BODY)
        p1 = next(b for b in dok["blocks"] if b["src"].get("pm") == 1)
        p1["style"].update({
            "frozen": False,
            "unlocked_in_freeze": True,
            "freeze_w": 170.0,
            "freeze_slot_h": 55.0,
            "leading": 3.0,
            "tracking": 0.1,
        })

        rendered = editor_blok.render_body(SAMPLE_BODY, dok)
        self.assertIn(
            "#beku(0pt, 0pt, freeze-w: 170pt, slot-h: 55pt, freeze-tracking: 0.1pt, freeze-leading: 3pt)[#pm(1);",
            rendered,
        )
        self.assertNotIn("#set text(tracking:", rendered)

        dok2 = self._parse_dok(rendered)
        p1_re = next(b for b in dok2["blocks"] if b["src"].get("pm") == 1)
        self.assertAlmostEqual(p1_re["style"]["freeze_w"], 170.0, places=2)
        self.assertAlmostEqual(p1_re["style"]["freeze_slot_h"], 55.0, places=2)
        self.assertAlmostEqual(p1_re["style"]["leading"], 3.0, places=2)
        self.assertAlmostEqual(p1_re["style"]["tracking"], 0.1, places=2)

    def test_multi_seg_table_omits_slot_h_when_unmoved(self):
        body_tbl = """#import "/template_jurnal.typ": *

]
#tabel(jumlah-kolom: 2, penuh: true, kepala: 1, di-tempat: true, keputusan: "tempat", nomor: 1, label: [Table 1.], isi: [Test])[
#table(
  columns: 2,
  table.header(table.cell()[A], table.cell()[B]),
  table.cell()[1], table.cell()[2],
)
]
#kolom[
#pm(1);After table.
]
"""
        dok = self._parse_dok(body_tbl)
        tbl = next(b for b in dok["blocks"] if b["jenis"] == "table")
        tbl["style"].update({
            "frozen": True,
            "freeze_x": 0.0,
            "freeze_y": 0.0,
            "freeze_slot_h": 120.0,
        })
        rendered = editor_blok.render_body(body_tbl, dok, multi_seg_ids={tbl["id"]})
        self.assertIn("boleh-pecah: true", rendered)
        self.assertNotIn("slot-h:", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
