"""独立审计必须发现重复响应、账本错配及伪装完成。"""
import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
from audit_model_comparison import audit, details, stage_metadata


class ComparisonAuditTests(unittest.TestCase):
    def test_stage_walk_does_not_double_count_cumulative_metadata(self):
        a = {'request_ids': ['a'], 'response_models': ['model-a']}
        b = {'request_ids': ['b'], 'response_models': ['model-a']}
        trial = {'item_id': 'x', 'round': 1, 'wall_seconds': 2, 'record': {'status': 'completed', 'metadata': {'request_ids': ['a', 'b']},
                 'refinement': {'baseline_metadata': a, 'review_metadata': b}}}
        result = details([trial])
        self.assertEqual(result['actual_request_ids'], ['a', 'b'])
        self.assertTrue(result['response_ids_unique'])
        trial['record']['refinement']['review_metadata']['request_ids'] = ['a']
        self.assertFalse(details([trial])['response_ids_unique'])

    def test_false_completion_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'results.json').write_text(json.dumps({'status': 'completed', 'trials': [], 'labels': {}, 'scores': {}}))
            (root / 'protocol.json').write_text(json.dumps({'sources': {}, 'labels_sha256': hashlib.sha256(b'{}').hexdigest(), 'requested_phase': 'all'}))
            (root / 'human-labels-scoring-only.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, '声称完成'): audit(root)


if __name__ == '__main__': unittest.main()
