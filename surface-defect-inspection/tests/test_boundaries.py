"""覆盖输入边界、缓存完整性、报告转义和并发运行的可观察行为。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from domain import Inspection
from imaging import discover, load_image, prepare_images
from inspect_images import main, validate_completed
from build_report import build


def negative(entries,*args):
    result=Inspection.model_validate({'linear_trace':'not_observed','material_loss':'not_observed','findings':[],
        'observations':[{'image_id':e['image_id'],'quality':'adequate','note':'已检查可见表面'} for e in entries],
        'limitations':['仅覆盖可见照片']})
    return result,{'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}


class BoundaryTests(unittest.TestCase):
    def run_cli(self,root,*extra):
        with patch.object(sys,'argv',['inspect','--input',str(root/'images'),'--output',str(root/'out'),*extra]),patch.dict(os.environ,{'DEEPSEEK_API_KEY':'unit-test-only'}),contextlib.redirect_stdout(io.StringIO()):
            return main()

    def test_empty_and_missing_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):discover(Path(folder))
            with self.assertRaises(ValueError):discover(Path(folder)/'missing')

    def test_unicode_single_view_and_exif_orientation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'中文 空格';root.mkdir()
            im=Image.new('RGB',(20,40),'white');exif=im.getexif();exif[274]=6
            path=root/'侧面.jpg';im.save(path,exif=exif)
            self.assertEqual(load_image(path).size,(40,20))
            self.assertEqual(len(discover(root)[root.name]),1)
            entries=prepare_images([path],'crops')
            self.assertEqual(entries[0]['source_size'],[40,20])
            self.assertTrue(all(e['data_url'].startswith('data:image/jpeg;base64,') for e in entries))

    def test_corrupt_image_fails_without_stopping_other_groups(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('a','b'):(root/'images'/name).mkdir(parents=True)
            (root/'images/a/bad.png').write_bytes(b'not an image')
            Image.new('RGB',(20,20),'white').save(root/'images/b/view.png')
            with patch('inspect_images.analyze',side_effect=negative):
                self.assertEqual(self.run_cli(root,'--workers','2'),1)
            records=json.loads((root/'out/summary.json').read_text(encoding='utf-8'))['records']
            self.assertEqual([r['status'] for r in records],['failed','completed'])

    def test_cached_record_is_reused_but_corrupt_mapping_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images/a').mkdir(parents=True)
            Image.new('RGB',(20,20),'white').save(root/'images/a/view.png')
            with patch('inspect_images.analyze',side_effect=negative) as model:
                self.assertEqual(self.run_cli(root),0)
                self.assertEqual(self.run_cli(root),0)
                self.assertEqual(model.call_count,1)
                path=root/'out/a.json';r=json.loads(path.read_text(encoding='utf-8'))
                r['input_images'][0]['source']='wrong.png'
                path.write_text(json.dumps(r),encoding='utf-8')
                self.assertEqual(self.run_cli(root),0)
                self.assertEqual(model.call_count,2)

    def test_source_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images/a').mkdir(parents=True)
            path=root/'images/a/view.png';Image.new('RGB',(20,20),'white').save(path)
            with patch('inspect_images.analyze',side_effect=negative) as model:
                self.run_cli(root)
                Image.new('RGB',(20,20),'black').save(path)
                self.run_cli(root)
                self.assertEqual(model.call_count,2)

    def test_report_is_portable_and_escapes_model_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images/a').mkdir(parents=True)
            path=root/'images/a/view.png';Image.new('RGB',(20,20),'white').save(path)
            with patch('inspect_images.analyze',side_effect=negative):self.run_cli(root)
            summary=root/'out/summary.json';d=json.loads(summary.read_text(encoding='utf-8'))
            d['records'][0]['result']['limitations']=['<script>alert(1)</script>']
            summary.write_text(json.dumps(d),encoding='utf-8')
            output=root/'report.html';build(root/'images',summary,output)
            page=output.read_text(encoding='utf-8')
            self.assertIn('data:image/jpeg;base64,',page)
            self.assertIn('&lt;script&gt;',page)
            self.assertNotIn('<script>alert(1)</script>',page)

    def test_no_key_stops_before_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images/a').mkdir(parents=True)
            Image.new('RGB',(20,20),'white').save(root/'images/a/view.png')
            with patch.dict(os.environ,{},clear=True),patch.object(sys,'argv',['inspect','--input',str(root/'images')]),patch('inspect_images.analyze') as model:
                with self.assertRaises(RuntimeError):main()
                model.assert_not_called()

    def test_invalid_resource_limits_rejected(self):
        for options in (['--workers','0'],['--workers','9'],['--timeout','nan'],['--timeout','0']):
            with patch.object(sys,'argv',['inspect','--input','unused',*options]):
                with self.assertRaises(ValueError):main()


if __name__=='__main__':unittest.main()
