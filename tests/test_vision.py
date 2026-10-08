import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from leanreview.vision import visual_review


class VisionTests(unittest.TestCase):
    def test_downsample_cached_review(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / 'ui.png'
            Image.new('RGB', (2048, 1800), 'white').save(image)
            def fake_run(cmd, **kwargs):
                self.assertIn('--image', cmd)
                with Image.open(Path(cmd[cmd.index('--image')+1])) as small:
                    self.assertLessEqual(max(small.size), 1024)
                self.assertEqual(cmd[-1], '-')
                self.assertNotIn('Review ONLY', ' '.join(cmd))
                Path(cmd[cmd.index('-o')+1]).write_text(json.dumps({'summary':'ok','findings':[]}))
                return SimpleNamespace(returncode=0,stderr='')
            with patch('leanreview.vision.shutil.which', return_value='/tmp/codex'):
                with patch('leanreview.vision.subprocess.run', side_effect=fake_run) as mock:
                    first, cache_hit, cache = visual_review(image, Path(td)/'.cache')
                    self.assertFalse(cache_hit)
                    self.assertTrue(cache.exists())
                    second, cache_hit, _ = visual_review(image, Path(td)/'.cache')
                    self.assertTrue(cache_hit)
                    self.assertEqual(first, second)
                    self.assertEqual(mock.call_count, 1)


if __name__ == '__main__':
    unittest.main()
