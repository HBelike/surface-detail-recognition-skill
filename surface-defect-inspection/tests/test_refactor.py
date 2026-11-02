"""改造的确定性验收：真值分母、区域几何、相反证据和数据隔离。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'evals'))


class RefactorTests(unittest.TestCase):
    def test_unknown_and_failure_are_not_exact(self):
        from assertions import grade_case
        truth = {'linear_trace': 'not_observed', 'material_loss': 'present'}
        grade = grade_case({'linear_trace': 'uncertain', 'material_loss': 'present'}, truth)
        self.assertFalse(grade['item_exact'])
        self.assertEqual(grade['category_correct'], 1)
        self.assertFalse(grade_case(None, truth)['item_exact'])

    def test_positive_and_negative_conflict_is_retained(self):
        from evidence_resolution import Claim, resolve_pair
        for left, right in [('not_observed', 'present'), ('present', 'not_observed')]:
            result = resolve_pair(Claim(left, ('a',)), Claim(right, ('b',)), same_region=True)
            self.assertTrue(result.needs_adjudication)
            self.assertEqual(result.state, 'uncertain')
            self.assertEqual(set(result.evidence_ids), {'a', 'b'})

    def test_other_surface_cannot_refute_positive(self):
        from evidence_resolution import Claim, resolve_pair
        result = resolve_pair(Claim('present', ('a',)), Claim('not_observed', ('b',)), same_region=False)
        self.assertEqual(result.state, 'present')
        self.assertFalse(result.needs_adjudication)

    def test_zoom_coordinate_and_invalid_boxes(self):
        from zoom_tools import CropRequest, execute_zoom, qwen_box
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '原图.png'
            Image.new('RGB', (1000, 500), 'white').save(path)
            request = CropRequest('原图.png', qwen_box([100, 200, 600, 700]), '检查局部')
            entry = execute_zoom(path, request)
            self.assertEqual(entry['crop_box'], [100, 100, 600, 350])
            for box in ([.5, .1, .2, .8], [-.1, 0, 1, 1], [0, 0, float('nan'), 1]):
                with self.assertRaises(ValueError): CropRequest('原图.png', box, '检查')
            with self.assertRaises(ValueError): execute_zoom(path, CropRequest('其他.png', [0, 0, 1, 1], '检查'))

    def test_annotation_percentages_and_rotation(self):
        from import_annotations import import_annotations
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'export.json'
            task = {'data': {'image_id': 'a/view.png'}, 'annotations': [{'was_cancelled': False, 'result': [
                {'type': 'rectanglelabels', 'original_width': 1000, 'original_height': 500, 'image_rotation': 0,
                 'value': {'x': 10, 'y': 20, 'width': 30, 'height': 40, 'rotation': 0, 'rectanglelabels': ['linear_trace']}}
            ]}]}
            path.write_text(json.dumps([task]), encoding='utf-8')
            manifest = {'images': {'a/view.png': {'size': [1000, 500], 'item_id': 'a', 'source': 'view.png', 'sha256': 'test'}}}
            result = import_annotations(path, manifest)
            self.assertEqual(result[0]['bbox_pixels'], [100, 100, 400, 300])
            task['annotations'][0]['result'][0]['image_rotation'] = 90
            path.write_text(json.dumps([task]), encoding='utf-8')
            with self.assertRaises(ValueError): import_annotations(path, manifest)

    def test_empty_reference_library_and_identity_isolation(self):
        from reference_images import load_references
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'normal.png'; Image.new('RGB', (40, 40), 'white').save(path)
            manifest = root / 'refs.json'
            manifest.write_text(json.dumps({'source': 'human', 'references': []}), encoding='utf-8')
            self.assertEqual(load_references('metal', {'a'}, manifest_path=manifest), [])
            import hashlib
            record = {'category': 'metal', 'item_id': 'a', 'path': 'normal.png', 'confirmed_normal': True,
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            manifest.write_text(json.dumps({'source': 'human', 'references': [record]}), encoding='utf-8')
            with self.assertRaises(ValueError): load_references('metal', {'a'}, manifest_path=manifest)
            with self.assertRaises(ValueError): load_references('metal', {'b'}, {record['sha256']}, manifest_path=manifest)


if __name__ == '__main__': unittest.main()
