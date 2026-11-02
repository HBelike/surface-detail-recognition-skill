"""防止无人工真值时虚构准确率，或通过拒判抬高召回率。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from evaluate import evaluate


class EvaluationTests(unittest.TestCase):
    def test_model_labels_rejected(self):
        with self.assertRaises(ValueError):
            evaluate({'source':'model','labels':[]},{'records':[]})

    def test_abstention_and_failure_reduce_recall(self):
        labels = [{'item_id':str(i),'linear_trace':'present','material_loss':'uncertain','reviewed':True} for i in range(3)]
        predictions = {'records':[{'item_id':'0','status':'completed','result':{'linear_trace':'present'}},
                                  {'item_id':'1','status':'completed','result':{'linear_trace':'uncertain'}}]}
        r = evaluate({'source':'human','labels':labels},predictions)
        c = r['classes']['linear_trace']
        self.assertEqual(c['fn'],2)
        self.assertAlmostEqual(c['recall_including_abstentions_and_failures'],1/3)
        self.assertAlmostEqual(c['decision_coverage'],1/3)
        self.assertIsNone(r['classes']['material_loss']['f1'])

    def test_unreviewed_label_not_negative(self):
        labels = [{'item_id':'a','linear_trace':'not_observed','reviewed':False}]
        r = evaluate({'source':'human','labels':labels},{'records':[]})
        self.assertEqual(r['classes']['linear_trace']['eligible_labels'],0)


if __name__ == '__main__':
    unittest.main()
