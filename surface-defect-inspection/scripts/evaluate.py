"""仅使用人工确认的组级标签评测，拒绝将缺失或不确定标签当作负例。"""
import argparse
import json
from pathlib import Path


def evaluate(truth, prediction):
    if truth.get('source') != 'human':
        raise ValueError('评测需要 source=human 的人工标签')
    labels = truth.get('labels', [])
    if len({x['item_id'] for x in labels}) != len(labels):
        raise ValueError('人工标签中物体ID重复')
    records = prediction['records']
    if len({x['item_id'] for x in records}) != len(records):
        raise ValueError('模型结果中物体ID重复')
    lookup = {x['item_id']:x for x in records}
    report = {'label_source':'human','classes':{}}
    for category in ('linear_trace','material_loss'):
        counts = dict(tp=0,fp=0,fn=0,tn=0,abstained=0,failed_or_missing=0,excluded_labels=0)
        positive_total = 0
        eligible = 0
        for label in labels:
            state = label.get(category)
            if label.get('reviewed') is not True or state not in ('present','not_observed'):
                counts['excluded_labels'] += 1
                continue
            eligible += 1
            actual = state == 'present'
            positive_total += int(actual)
            record = lookup.get(label['item_id'])
            if not record or record.get('status') != 'completed' or record.get('refinement',{}).get('status')=='failed':
                counts['failed_or_missing'] += 1
                counts['fn'] += int(actual)
                continue
            predicted = record['result'][category]
            if predicted not in ('present','not_observed','uncertain'):
                raise ValueError('模型结果包含非法类别状态')
            if predicted == 'uncertain':
                counts['abstained'] += 1
                counts['fn'] += int(actual)
                continue
            if actual:
                counts['tp' if predicted=='present' else 'fn'] += 1
            else:
                counts['fp' if predicted=='present' else 'tn'] += 1
        tp, fp, fn, tn = (counts[k] for k in ('tp','fp','fn','tn'))
        precision = tp/(tp+fp) if tp+fp else None
        recall = tp/positive_total if positive_total else None
        f1 = 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
        decided = eligible-counts['abstained']-counts['failed_or_missing']
        report['classes'][category] = {**counts,'eligible_labels':eligible,'precision':precision,
            'recall_including_abstentions_and_failures':recall,'f1':f1,
            'decision_coverage':decided/eligible if eligible else None,
            'accuracy_on_decided':(tp+tn)/decided if decided else None,
            'accuracy_all_labels':(tp+tn)/eligible if eligible else None}
    report['note'] = '人工uncertain/未核对标签排除；模型uncertain及失败降低覆盖率，人工阳性上的拒判/失败计入漏检。无有效分母时指标为null。'
    classes=list(report['classes'].values())
    total={key:sum(c[key] for c in classes) for key in ('tp','fp','fn','tn','eligible_labels','abstained','failed_or_missing')}
    pden=total['tp']+total['fp'];rden=total['tp']+total['fn'];fden=2*total['tp']+total['fp']+total['fn']
    valid_f1=[c['f1'] for c in classes if c['f1'] is not None]
    report['aggregate']={'macro_f1':sum(valid_f1)/len(valid_f1) if valid_f1 else None,
                         'micro_precision':total['tp']/pden if pden else None,
                         'micro_recall':total['tp']/rden if rden else None,
                         'micro_f1':2*total['tp']/fden if fden else None,
                         'decision_coverage':(total['eligible_labels']-total['abstained']-total['failed_or_missing'])/total['eligible_labels'] if total['eligible_labels'] else None,
                         'counts':total}
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'evals'))
    from assertions import grade_summary
    report['exact_match']=grade_summary(prediction,truth)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--labels',type=Path,required=True)
    p.add_argument('--predictions',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    report = evaluate(json.loads(a.labels.read_text(encoding='utf-8-sig')),json.loads(a.predictions.read_text(encoding='utf-8')))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
