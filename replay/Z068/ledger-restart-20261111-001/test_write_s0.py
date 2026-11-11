"""Synthetic only; no actual plan, source path, project imports, or network."""
import tempfile
import unittest
from pathlib import Path
from write_s0 import decode, encode, exclusive, project, jsonl, make_controls


class SyntheticTests(unittest.TestCase):
    def row(self, i):
        return dict(chunk_id=f'fake-{i}', source_file='fake.pdf', page_range=[1],
                    chunk_text='完全虚构测试句。', chunk_index=i, annotation={'secret': 'never returned'})

    def test_projection(self):
        rows, offsets = project(jsonl([self.row(2), self.row(1)]), 2)
        self.assertEqual([r['chunk_index'] for r in rows], [2, 1])
        self.assertNotIn('annotation', rows[0])
        self.assertEqual(len(rows[0]), 5)
        self.assertNotIn('chunk_text', offsets[0])

    def test_duplicate_key(self):
        with self.assertRaises(ValueError):
            decode(b'{"a":1,"a":2}')

    def test_invalid_line(self):
        with self.assertRaisesRegex(ValueError, 'blank'):
            project(jsonl([self.row(1)]) + b'\n', 1)

    def test_invalid_count_and_duplicate(self):
        with self.assertRaises(ValueError):
            project(jsonl([self.row(1)]), 2)
        with self.assertRaises(ValueError):
            project(jsonl([self.row(1), self.row(1)]), 2)

    def test_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'new'
            exclusive(p, b'synthetic')
            with self.assertRaises(FileExistsError):
                exclusive(p, b'replacement')
            self.assertEqual(p.read_bytes(), b'synthetic')

    def test_controls_do_not_infer_others(self):
        sources = []
        for n in range(150):
            sid = 'ziwei_068' if n == 0 else f'fake_{n}'
            sources.append({'source_id': sid, 'steps': {f'S{x}': {} for x in range(8)}})
        result = make_controls({}, {'sources': sources}, 'fake-run', 'unknown', {}, {})
        self.assertEqual(len(result['resume_inventory.json']['records']), 1200)
        self.assertEqual(result['status.json']['sources'][1]['steps']['S0']['decision'], 'needs_confirmation')
        self.assertEqual(result['status.json']['sources'][0]['steps']['S0']['status'], 'not_started')


if __name__ == '__main__':
    unittest.main()
