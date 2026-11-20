import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
if not (BASE / 'build.py').exists():
    BASE = Path('/opt/typst_editor')
spec = importlib.util.spec_from_file_location('engine', BASE / 'build.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class NativeTablesTest(unittest.TestCase):
    def test_nested_wrappers_are_removed_without_overlapping_edits(self):
        text = '#figure(align(center)[#table(columns: 1, [#figure(align(center)[#table(columns: 1, [Nested])], kind: table)])], kind: table)'
        result, count = engine.buka_figure_tabel(text)
        self.assertEqual(count, 2)
        self.assertEqual(result, '#table(columns: 1, [#table(columns: 1, [Nested])])')

    def test_rowspan_across_first_row_is_not_an_invalid_header(self):
        html = '<table><tr><td rowspan="2">Shared</td><td>First</td></tr><tr><td>Last</td></tr></table>'
        ast = json.loads(subprocess.check_output(['pandoc', '-f', 'html', '-t', 'json'], input=html.encode()))
        with tempfile.TemporaryDirectory() as folder:
            text = engine.tabel_ast_typst(ast, folder)['blocks'][0]['c'][1]
            text, _ = engine.buka_figure_tabel(text)
            self.assertNotIn('table.header', text)
            source = Path(folder) / 'main.typ'
            source.write_text(text)
            subprocess.run(['typst', 'compile', str(source), str(source.with_suffix('.pdf'))], check=True, capture_output=True)

    def test_table_rule_is_not_a_footer_but_real_overflow_is_rejected(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            for baseline, expected in ((740, False), (770, True)):
                pdf = Path(folder) / ('page%s.pdf' % baseline)
                doc = pymupdf.open()
                page = doc.new_page(width=595.28, height=841.89)
                page.draw_line((76, 720), (518.2, 720))
                page.draw_line((76, 765), (518.2, 765))
                page.insert_text((76, baseline), 'Visible body text', fontsize=11)
                doc.save(pdf)
                doc.close()
                self.assertEqual(bool(engine.baris_luber(str(pdf))), expected)

    def test_long_table_repeats_heading_without_printing_markup(self):
        import pymupdf
        html = '<table><thead><tr><th>Heading</th></tr></thead><tbody>' + ''.join(
            '<tr><td>Row %s</td></tr>' % i for i in range(100)) + '</tbody></table>'
        ast = json.loads(subprocess.check_output(['pandoc', '-f', 'html', '-t', 'json'], input=html.encode()))
        with tempfile.TemporaryDirectory() as folder:
            result = engine.tabel_ast_typst(ast, folder)['blocks'][0]['c'][1]
            result, _ = engine.buka_figure_tabel(result)
            source = Path(folder) / 'main.typ'
            source.write_text('#set page(height: 300pt)\n' + result)
            subprocess.run(['typst', 'compile', str(source), str(source.with_suffix('.pdf'))], check=True, capture_output=True)
            with pymupdf.open(source.with_suffix('.pdf')) as doc:
                self.assertGreater(len(doc), 1)
                for page in doc:
                    self.assertIn('Heading', page.get_text())
                    self.assertNotIn('<table', page.get_text())

    def test_html_spans_survive_to_compiled_typst(self):
        html = '<table><tr><th colspan="2">Head</th></tr><tr><td rowspan="2">Shared</td><td>First</td></tr><tr><td>Last</td></tr></table>'
        api = json.loads(subprocess.check_output(['pandoc', '-f', 'html', '-t', 'json'], input=html.encode()))
        api['blocks'] = [{'t': 'CodeBlock', 'c': [['', ['html'], []], html]}]
        ast = engine.normalkan_tabel_html(api)
        self.assertEqual(ast['blocks'][0]['t'], 'Table')
        with tempfile.TemporaryDirectory() as folder:
            result = engine.tabel_ast_typst(ast, folder)['blocks'][0]['c'][1]
            result, count = engine.buka_figure_tabel(result)
            self.assertEqual(count, 1)
            self.assertIn('rowspan: 2', result)
            self.assertIn('colspan: 2', result)
            self.assertIn('x: 1, y: 2', result)
            self.assertNotIn('<table', result)
            source = Path(folder) / 'main.typ'
            source.write_text(result)
            subprocess.run(['typst', 'compile', str(source), str(source.with_suffix('.pdf'))], check=True, capture_output=True)

    def test_narrative_indent_and_references_are_not_interviews(self):
        def para(t): return {'t': 'Para', 'c': [{'t': 'Str', 'c': t}]}
        narrative = para('This study involved guest lecturers. ' * 10)
        ref = para('Smith, J. (2026). A reference.')
        blocks = [para('Title'), para('Keywords: a; b; c'),
                  {'t': 'BlockQuote', 'c': [narrative]},
                  {'t': 'Header', 'c': [1, ['', [], []], [{'t': 'Str', 'c': 'REFERENCES'}]]},
                  {'t': 'BlockQuote', 'c': [ref]}]
        out = engine.buka_blok_pandoc(blocks)
        self.assertIn(narrative, out)
        self.assertIn(ref, out)


if __name__ == '__main__': unittest.main()
