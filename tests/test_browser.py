"""Integration test using a local web server and actual Playwright Chromium."""
import tempfile
import unittest
from pathlib import Path
from leanreview.ui import audit_ui


class BrowserSmoke(unittest.TestCase):
    def test_local_ui_audit(self):
        fixture = Path(__file__).resolve().parents[1] / 'examples'
        with tempfile.TemporaryDirectory() as td:
                out = Path(td) / 'ui'
                base = 'http://localhost'
                report = audit_ui(base, ['/broken.html'], [390, 1440], out, static_dir=fixture)
                self.assertEqual(len(report['screens']), 2)
                self.assertTrue((out / 'review.html').is_file())
                mobile = report['screens'][0]
                self.assertTrue((out / mobile['image']).is_file())
                self.assertIn('horizontal-overflow', [f['rule'] for f in mobile['issues']])
                self.assertIn('broken-images', [f['rule'] for f in mobile['issues']])
                self.assertEqual(mobile['dom']['h1Count'], 1)
                audit_ui(base, ['/broken.html'], [390], out, update_baseline=True, static_dir=fixture)
                second = audit_ui(base, ['/broken.html'], [390], out, baseline=out / 'baseline', static_dir=fixture)
                self.assertEqual(second['screens'][0]['comparison']['status'], 'unchanged')
                # In-memory HTML render validates embedded review-page JavaScript.
                from playwright.sync_api import sync_playwright
                from shutil import which
                with sync_playwright() as pw:
                    launch_args = {'executable_path': which('chromium')} if which('chromium') else {}
                    b = pw.chromium.launch(headless=True, **launch_args)
                    pg = b.new_page()
                    failures = []
                    pg.on('pageerror', lambda e: failures.append(str(e)))
                    pg.set_content((out / 'review.html').read_text(encoding='utf-8'))
                    self.assertEqual(len(pg.locator('.card').all()), 1)
                    import base64
                    sample = base64.b64encode((out / second['screens'][0]['image']).read_bytes()).decode('ascii')
                    pg.locator('img[data-screen]').evaluate(
                        "(el,src)=>{el.src=src}", 'data:image/png;base64,'+sample)
                    pg.locator('img[data-screen]').click(position={'x':80,'y':80})
                    self.assertTrue(pg.locator('dialog').evaluate('(d)=>d.open'))
                    pg.locator('#comment').fill('Please fix mobile overflow')
                    pg.locator('#save').click()
                    self.assertEqual(pg.locator('.notes div').count(),1)
                    self.assertEqual(failures, [])
                    b.close()


if __name__ == '__main__':
    unittest.main()
