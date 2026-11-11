import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from write_s1_v2 import RELATIVE_OUTPUTS, publication, setup_paths, decode


class Tests(unittest.TestCase):
    def prepare(self, root):
        for p in setup_paths(root):p.mkdir(parents=True, exist_ok=True)
        (root/'status.json').write_bytes(b'old synthetic status')
        return {rel: ('fake payload '+str(i)).encode() for i,rel in enumerate(RELATIVE_OUTPUTS)}

    def test_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);payloads=self.prepare(root)
            result=publication(root,payloads,b'old synthetic status',b'new synthetic status')
            self.assertEqual(len(result),7)
            self.assertEqual((root/'status.json').read_bytes(),b'new synthetic status')
            self.assertEqual((root/'history/pre_s1_manual_completion/status.json').read_bytes(),b'old synthetic status')
            events=[decode(x) for x in (root/'S1_publication_journal.jsonl').read_bytes().splitlines()]
            verified=[i for i,e in enumerate(events) if e['event']=='temp_verified']
            installed=[i for i,e in enumerate(events) if e['event']=='install_intent']
            self.assertEqual(len(verified),6);self.assertLess(max(verified),min(installed));self.assertEqual(events[-1]['event'],'complete')

    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);payloads=self.prepare(root);p=root/RELATIVE_OUTPUTS[0];p.write_bytes(b'occupied')
            with self.assertRaises(ValueError):publication(root,payloads,b'old synthetic status',b'new')
            self.assertEqual(p.read_bytes(),b'occupied');self.assertFalse((root/'S1_publication_journal.jsonl').exists())

    def test_status_guard(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);payloads=self.prepare(root)
            with self.assertRaises(ValueError):publication(root,payloads,b'wrong',b'new')
            self.assertFalse((root/'S1_publication_journal.jsonl').exists())

    def test_missing_parent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'status.json').write_bytes(b'old')
            with self.assertRaises(ValueError):publication(root,{x:b'fake' for x in RELATIVE_OUTPUTS},b'old',b'new')
            self.assertFalse((root/'S1_publication_journal.jsonl').exists())

    def test_failure_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);payloads=self.prepare(root)
            with patch('write_s1_v2.os.link',side_effect=OSError('synthetic installation failure')):
                with self.assertRaises(OSError):publication(root,payloads,b'old synthetic status',b'new')
            self.assertEqual((root/'status.json').read_bytes(),b'old synthetic status')
            self.assertTrue((root/'history/pre_s1_manual_completion/status.json').exists())
            self.assertEqual(len(list(root.rglob('*.s1.tmp'))),6)
            with self.assertRaises(ValueError):publication(root,payloads,b'old synthetic status',b'new')


if __name__=='__main__':unittest.main()
