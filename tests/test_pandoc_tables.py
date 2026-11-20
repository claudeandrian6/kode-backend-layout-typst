"""Pandoc markup content is not code; cleanup must tolerate unknown table syntax."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("table_engine",BASE/"build.py")
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)


class PandocTableTest(unittest.TestCase):
    def test_literal_parentheses_in_cell(self):
        table=r"#table(columns: 1, align: left, [Platforms \(Zoom, LMS, and Docs) work.])"
        self.assertEqual(engine.akhir_kurung_typst(table,table.index("(")),len(table)-1)
        fixed=engine.gaya_tabel_typst(table)
        self.assertNotIn("align:",fixed)
        self.assertIn(r"\(Zoom, LMS, and Docs)",fixed)

    def test_unpaired_quote_and_parenthesis_are_text_inside_content(self):
        table='#table(columns: 1, [A quote " and a closing ) and a brace } are text.])'
        self.assertEqual(engine.akhir_kurung_typst(table,table.index("(")),len(table)-1)

    def test_code_strings_and_links_keep_delimiters(self):
        table='#table(columns: 1, inset: 6pt, [#link("https://example.org/(x)?q=]")[A (link)]])'
        fixed=engine.gaya_tabel_typst(table)
        self.assertNotIn("inset:",fixed)
        self.assertIn('#link("https://example.org/(x)?q=]")[A (link)]',fixed)

    def test_multiline_style_arguments_are_removed_as_whole_arguments(self):
        table="#table(\ncolumns: 2,\nalign: (c,r) => (\n auto, auto,\n).at(c),\ninset: (x: 6pt,\n y: 3pt),\n[Alpha], [1],\n)"
        fixed=engine.gaya_tabel_typst(table)
        self.assertEqual(fixed,"#table(\ncolumns: 2,\n[Alpha], [1],\n)")

    def test_nested_tables_are_not_corrupted_by_overlapping_edits(self):
        table="#table(columns: 1, align: left, [#table(columns: 1, inset: 6pt, [Nested])])"
        fixed=engine.gaya_tabel_typst(table)
        self.assertEqual(fixed,"#table(columns: 1, [#table(columns: 1, [Nested])])")

    def test_cell_lines_named_align_and_inset_are_preserved(self):
        table="#table(\ncolumns: 1,\nalign: left,\n[Notes:\nalign: keep this text\ninset: keep this text too\n],\n)"
        fixed=engine.gaya_tabel_typst(table)
        self.assertIn("align: keep this text",fixed)
        self.assertIn("inset: keep this text too",fixed)
        self.assertNotIn("align: left",fixed)

    def test_incomplete_table_is_preserved_without_exception(self):
        table="#table(columns: 1, align: left, [Missing close]"
        self.assertIsNone(engine.akhir_kurung_typst(table,table.index("(")))
        self.assertEqual(engine.gaya_tabel_typst(table),table)

    def test_incomplete_first_table_does_not_block_later_table(self):
        prefix="#table(columns: 1, align: left, [Broken]\n"
        valid="#table(columns: 1, inset: 6pt, [Valid])"
        self.assertEqual(engine.gaya_tabel_typst(prefix+valid),
                         prefix+"#table(columns: 1, [Valid])")

    def test_comments_raw_and_math_do_not_change_balance(self):
        raw=chr(96)
        table=('#table(columns: 1, /* nested /* ) ] */ comment */\n'
               'align: left, [Code '+raw+') ] }'+raw+' and math $x_(i)$],\n'
               '// ) ] ignored\n)')
        self.assertEqual(engine.akhir_kurung_typst(table,table.index("(")),len(table)-1)

    def test_unrecognized_string_is_left_unchanged(self):
        table='#table(columns: 1, align: "unclosed string)'
        self.assertEqual(engine.gaya_tabel_typst(table),table)

    def test_actual_575_tables_compile_after_cleanup(self):
        source=(BASE/"tests/fixtures/pandoc575_tables.typ").read_text()
        fixed=engine.gaya_tabel_typst(source)
        self.assertEqual(fixed.count("#table("),2)
        self.assertIn(r"\(Zoom, LMS, and Docs)",fixed)
        self.assertIn("GL is useless",fixed)
        with tempfile.TemporaryDirectory(dir=BASE,prefix="pandoc-table-test-") as directory:
            main=Path(directory)/"main.typ";pdf=Path(directory)/"test.pdf"
            main.write_text('#set text(font: "Times New Roman", size: 11pt)\n'+fixed)
            rc,_,err=engine.jalankan(["/usr/local/bin/typst","compile","--root",str(BASE),
                                      "--font-path",str(BASE/"fonts"),str(main),str(pdf)])
            self.assertEqual(rc,0,err)
            self.assertGreater(pdf.stat().st_size,1000)


if __name__=="__main__":
    unittest.main()
