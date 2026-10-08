import json
import tempfile
import unittest
from pathlib import Path
from leanreview.ui import (
    validate_url, safe_route, build_html_report, feedback_prompt, compare_images, _issues,
)


class UITests(unittest.TestCase):
    def test_local_only(self):
        validate_url('http://127.0.0.1:4200', False)
        validate_url('http://localhost:5173', False)
        with self.assertRaises(ValueError):
            validate_url('https://example.org', False)
        validate_url('https://example.org', True)

    def test_reject_credentials(self):
        with self.assertRaises(ValueError):
            validate_url('http://bob:password@localhost:4200', True)

    def test_routes(self):
        safe_route('/login')
        for r in ('//example.com', 'login', '/abc?token=abc', '/abc#frag', '/foo\\bar'):
            with self.assertRaises(ValueError):
                safe_route(r)

    def test_html_escaping(self):
        data = {'screens': [{'route': '</h2><script>alert(1)</script>', 'width': 390,
                             'image': 'test.png', 'issues': [], 'dom': {'title':'<b>bad</b>'}}]}
        h = build_html_report(data)
        self.assertNotIn('<script>alert(1)</script>', h)
        self.assertNotIn('<b>bad</b>', h)
        self.assertIn('Export feedback JSON', h)

    def test_feedback_with_audit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / 'notes.json').write_text(json.dumps({'feedback': [
                {'screen': 0, 'x': 20, 'y': 50, 'note': 'Button misaligned'}]}))
            (base / 'audit.json').write_text(json.dumps({'screens': [{'route':'/home', 'width':390}]}))
            output = feedback_prompt(base/'notes.json', base/'audit.json')
            self.assertIn('Button misaligned', output)
            self.assertIn('/home', output)
            self.assertIn('390px', output)

    def test_feedback_invalid_coordinates_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td)/'f.json'
            f.write_text(json.dumps({'feedback':[{'screen':0,'x':999,'y':5,'note':'fake'}]}))
            self.assertNotIn('fake', feedback_prompt(f))

    def test_issues(self):
        issues = _issues({'overflowX':True, 'brokenImages':1, 'hasLang':False, 'h1Count':0}, ['boom'], [])
        self.assertIn('horizontal-overflow', [i['rule'] for i in issues])
        self.assertIn('javascript-error', [i['rule'] for i in issues])

    def test_baseline_compare(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/'a.png'; b=Path(td)/'b.png'
            Image.new('RGB',(40,40),'white').save(a)
            Image.new('RGB',(40,40),'black').save(b)
            self.assertEqual(compare_images(a,b)['status'], 'changed')
            self.assertEqual(compare_images(a,a)['status'], 'unchanged')


if __name__ == '__main__':
    unittest.main()
