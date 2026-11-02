"""同一人工标签上的候选对照；开发分数不代表独立泛化验收。"""
import argparse
import json
from pathlib import Path
from evaluate import evaluate
from inspect_images import atomic_json


def compare(labels,runs,dataset_role='development'):
    truth=json.loads(labels.read_text(encoding='utf-8-sig'))
    reports=[]
    for run in runs:
        path=run/'summary.json' if run.is_dir() else run
        pred=json.loads(path.read_text(encoding='utf-8'));metrics=evaluate(truth,pred)
        actual=[r['item_id'] for r in pred['records']];expected=pred.get('expected_items',actual)
        complete=len(actual)==len(set(actual)) and set(actual)==set(expected)
        checks={}
        for category,c in metrics['classes'].items():
            checks[category]={'precision':c['precision'] is not None and c['precision']>=.90,
                              'recall':c['recall_including_abstentions_and_failures'] is not None and c['recall_including_abstentions_and_failures']>=.85,
                              'coverage':c['decision_coverage'] is not None and c['decision_coverage']>=.95,
                              'has_positive_and_negative_labels':c['tp']+c['fn']>0 and c['eligible_labels']-(c['tp']+c['fn'])>0}
        macro=metrics['aggregate']['macro_f1']
        met=complete and all(all(v.values()) for v in checks.values()) and macro is not None and macro>=.87
        reports.append({'run':str(path.parent),'pipeline':pred.get('pipeline',pred.get('configuration',{}).get('mode')),
                        'configuration':pred.get('configuration',{}),'complete':complete,'records':len(actual),
                        'checks':checks,'target_met_on_available_labels':met,'metrics':metrics,
                        'actual_run_execution':pred.get('execution'),
                        'baseline_reused':bool(pred.get('baseline_reuse_notice')),
                        'states':[{'item_id':r['item_id'],'execution_status':r['status'],
                                   'review_status':r.get('refinement',{}).get('status'),
                                   'linear_trace':r.get('result',{}).get('linear_trace'),
                                   'material_loss':r.get('result',{}).get('material_loss')} for r in pred['records']]})
    return {'schema_version':'1.0','dataset_role':dataset_role,'label_source':str(labels),
            'independent_generalization_proven':False,
            'note':'结果仅比较给定标签；达到开发目标不代表对未见数据达标。复用历史初判的核验实验与完整新推理分开。',
            'runs':reports}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--labels',type=Path,required=True);p.add_argument('--runs',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--dataset-role',choices=['development','validation','held_out'],default='development')
    args=p.parse_args();report=compare(args.labels,args.runs,args.dataset_role);atomic_json(args.output,report)
    lines=['# 候选流程对照','','数据角色：'+args.dataset_role+'。仅使用人工标签；开发目标通过不等于泛化验收。',
           '','|候选|完整|线状P/R/F1/覆盖率|缺损P/R/F1/覆盖率|现有标签上目标|','|---|---|---|---|---|']
    def values(c):
        return '/'.join('N/A' if v is None else f'{v:.1%}' for v in [c['precision'],c['recall_including_abstentions_and_failures'],c['f1'],c['decision_coverage']])
    for r in report['runs']:
        lines.append('|'+ '|'.join([Path(r['run']).name,str(r['complete']),values(r['metrics']['classes']['linear_trace']),values(r['metrics']['classes']['material_loss']),str(r['target_met_on_available_labels'])])+'|')
    args.output.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'runs':len(report['runs']),'meeting_development_target':sum(r['target_met_on_available_labels'] for r in report['runs'])}))


if __name__=='__main__':main()
