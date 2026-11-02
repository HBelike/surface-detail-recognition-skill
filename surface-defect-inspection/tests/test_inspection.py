"""测试影响结果可信度的关键不变量。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from domain import Finding, Inspection, validate_image_ids
from imaging import map_box, subject_box
from PIL import Image, ImageDraw


class ContractTests(unittest.TestCase):
    def sample(self):
        return {'linear_trace':'not_observed','material_loss':'not_observed','findings':[],
                'observations':[{'image_id':'a','quality':'adequate','note':'可见表面已检查'}],
                'limitations':['照片可见范围']}

    def test_present_requires_evidence(self):
        data = self.sample()
        data['linear_trace'] = 'present'
        with self.assertRaises(ValueError):
            Inspection.model_validate(data)

    def test_bad_coordinates_rejected(self):
        for bbox in ([.2,.3,.1,.9],[0,0,2,1],[0,0,0,1],[0,0,float('nan'),1]):
            with self.assertRaises(ValueError):
                Finding(category='linear_trace',image_id='a',bbox=bbox,description='细痕',certainty='confirmed')

    def test_unknown_image_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ids(Inspection.model_validate(self.sample()), {'b'})

    def test_uncertain_is_not_negative(self):
        data = self.sample()
        data['linear_trace'] = 'uncertain'
        self.assertEqual(Inspection.model_validate(data).linear_trace,'uncertain')

    def test_all_limited_cannot_be_negative(self):
        data = self.sample()
        data['observations'][0]['quality'] = 'limited'
        with self.assertRaises(ValueError):
            Inspection.model_validate(data)

    def test_confirmed_evidence_cannot_be_negative(self):
        data = self.sample()
        data['findings'] = [dict(category='linear_trace',image_id='a',bbox=[0,0,.5,.5],description='细痕',certainty='confirmed')]
        with self.assertRaises(ValueError):
            Inspection.model_validate(data)

    def test_crop_maps_to_original(self):
        entry = {'crop_box':[100,200,500,600]}
        self.assertEqual(map_box([.25,.25,.75,.75],entry),[200,300,400,500])

    def test_subpixel_box_keeps_positive_area(self):
        result = map_box([.50001,.50001,.50002,.50002],{'crop_box':[0,0,100,100]})
        self.assertLess(result[0],result[2])
        self.assertLess(result[1],result[3])

    def test_subject_box_preserves_synthetic_object(self):
        im = Image.new('RGB',(1000,1000),'white')
        ImageDraw.Draw(im).rectangle((350,250,650,750),fill='black')
        x1,y1,x2,y2 = subject_box(im)
        self.assertLessEqual(x1,350)
        self.assertLessEqual(y1,250)
        self.assertGreaterEqual(x2,650)
        self.assertGreaterEqual(y2,750)


if __name__ == '__main__':
    unittest.main()
