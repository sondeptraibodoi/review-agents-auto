import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from leanreview.ai import codex_review


class AITests(unittest.TestCase):
    def test_isolated_and_prompt_via_stdin(self):
        def fake_run(cmd, **kwargs):
            self.assertIn('--skip-git-repo-check', cmd)
            self.assertIn('--sandbox', cmd)
            self.assertEqual(cmd[-1], '-')
            self.assertNotIn('PRIVATE_PATCH', ' '.join(cmd))
            self.assertIn('PRIVATE_PATCH', kwargs['input'])
            self.assertNotEqual(kwargs['cwd'], Path('/my/repo'))
            Path(cmd[cmd.index('-o')+1]).write_text(json.dumps({'summary':'ok','findings':[]}))
            return SimpleNamespace(returncode=0, stderr='')
        with patch('leanreview.ai.shutil.which', return_value='/bin/codex'):
            with patch('leanreview.ai.subprocess.run', side_effect=fake_run):
                result=codex_review('PRIVATE_PATCH', Path('/my/repo'))
                self.assertEqual(result['summary'],'ok')

    def test_missing_codex(self):
        with patch('leanreview.ai.shutil.which', return_value=None):
            with self.assertRaises(RuntimeError):
                codex_review('patch')


if __name__ == '__main__':
    unittest.main()
