"""人工矩形上的有限位置检查，不把坐标合法当作视觉正确。"""
import argparse
import json
from pathlib import Path


def iou(left, right):
    intersection = max(0, min(left[2], right[2]) - max(left[0], right[0])) * max(0, min(left[3], right[3]) - max(left[1], right[1]))
    areas = [(b[2] - b[0]) * (b[3] - b[1]) for b in (left, right)]
    return intersection / (sum(areas) - intersection) if sum(areas) - intersection else 0


def evaluate_localization(summary, truth):
    if truth.get('source') != 'human': raise ValueError('定位评测仅接受人工标注')
    annotations = truth.get('annotations', [])
    if not annotations: return {'status': 'not_evaluated', 'reason': '没有人工位置框'}
    records = summary.get('records', [])
    if len({r['item_id'] for r in records}) != len(records): raise ValueError('模型物体ID重复')
    lookup = {r['item_id']: r for r in records}; used = set(); matches = []
    for annotation in annotations:
        if annotation.get('source_type') != 'human': raise ValueError('存在非人工位置真值')
        category = annotation['category']
        if category not in ('linear_trace', 'material_loss'): continue
        record = lookup.get(annotation['item_id'], {})
        completed = record.get('status') == 'completed' and record.get('refinement', {}).get('status') != 'failed'
        candidates = record.get('evidence', []) if completed else []
        eligible = [(idx, p) for idx, p in enumerate(candidates) if p['category'] == category and p['source'] == annotation['source'] and p['certainty'] == 'confirmed'
                    and (annotation['item_id'], idx) not in used]
        eligible.sort(key=lambda pair: iou(annotation['bbox_pixels'], pair[1]['bbox_pixels']), reverse=True)
        score = iou(annotation['bbox_pixels'], eligible[0][1]['bbox_pixels']) if eligible else 0
        hit = False
        if score > 0:
            idx, predicted = eligible[0]; used.add((annotation['item_id'], idx))
            x1, y1, x2, y2 = predicted['bbox_pixels']; x, y = (x1 + x2) / 2, (y1 + y2) / 2
            a = annotation['bbox_pixels']; hit = a[0] <= x <= a[2] and a[1] <= y <= a[3]
        matches.append({'image_id': annotation['image_id'], 'category': category, 'iou': score, 'region_hit': hit})
    return {'status': 'evaluated_annotated_regions_only', 'positive_boxes': len(matches), 'matches': matches,
            'region_hit_rate': sum(m['region_hit'] for m in matches) / len(matches) if matches else None,
            'mean_iou': sum(m['iou'] for m in matches) / len(matches) if matches else None,
            'note': '仅在已人工标注区域评估，同图同类一对一贪心匹配；区域命中指预测框中心落入人工框。细线IoU受框宽影响，不据此推算全图误报率。'}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary',type=Path,required=True);p.add_argument('--annotations',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=evaluate_localization(json.loads(a.summary.read_text(encoding='utf-8')),json.loads(a.annotations.read_text(encoding='utf-8')))
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
