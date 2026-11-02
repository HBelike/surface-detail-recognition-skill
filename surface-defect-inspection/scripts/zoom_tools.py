"""借鉴Qwen区域工具契约；本地只读裁剪，不复制模型专用像素预算。"""
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from imaging import encode_image, load_image, map_box, subject_box


@dataclass(frozen=True)
class CropRequest:
    image_id: str
    bbox: list[float]
    question: str

    def __post_init__(self):
        if not self.image_id or not self.question.strip(): raise ValueError('区域请求需要图片ID与核验问题')
        if len(self.bbox) != 4 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in self.bbox):
            raise ValueError('区域坐标必须是四个有限数值')
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1): raise ValueError('区域坐标必须为0至1的正面积xyxy')


def qwen_box(box):
    if len(box) != 4: raise ValueError('Qwen区域必须有四个坐标')
    normalized = [v / 1000 for v in box]
    return CropRequest('validate', normalized, '校验坐标').bbox


def execute_zoom(path: Path, request: CropRequest):
    path = Path(path)
    if request.image_id != path.name: raise ValueError('区域请求的图像必须属于指定原图')
    im = load_image(path)
    box = map_box(request.bbox, {'crop_box': [0, 0, *im.size]})
    request_id = hashlib.sha256(json.dumps([path.name, box, request.question], ensure_ascii=False).encode()).hexdigest()[:16]
    return {'image_id': f'{path.name}::zoom-{request_id}', 'source': path.name,
            'source_size': list(im.size), 'crop_box': box, 'request_id': request_id,
            'question': request.question, 'data_url': encode_image(im.crop(box))}


def evidence_id(category, source, box):
    return 'e-' + hashlib.sha256(json.dumps([category, source, box], ensure_ascii=False).encode()).hexdigest()[:20]


def overlap_fraction(inner, outer):
    area = max(0, min(inner[2], outer[2]) - max(inner[0], outer[0])) * max(0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    return area / ((inner[2] - inner[0]) * (inner[3] - inner[1]))


def plan_zoom(paths, baseline, max_crops=6):
    """登记全部候选，优先观察受限和细小区域；预算外候选明确保留。"""
    if not 1 <= max_crops <= 6: raise ValueError('裁剪预算必须为1至6')
    lookup = {e['image_id']: e for e in baseline['input_images']}
    path_map = {p.name: p for p in paths}
    if len(path_map) != len(paths): raise ValueError('原图文件名重复')
    images = {p.name: load_image(p) for p in paths}
    records = []; requests = []; triggers = []
    for category in ('linear_trace', 'material_loss'):
        if baseline['result'][category] == 'uncertain': triggers.append(category + ':uncertain')
    for finding in baseline['result']['findings']:
        entry = lookup[finding['image_id']]; source = entry['source']; box = map_box(finding['bbox'], entry)
        record = {**finding, 'source': source, 'bbox_pixels': box,
                  'evidence_id': evidence_id(finding['category'], source, box), 'stage': 'overview'}
        if not any(r['evidence_id'] == record['evidence_id'] for r in records): records.append(record)
        triggers.append(finding['category'] + ':candidate_requires_check')
        im = images[source]; x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        hx, hy = max(128, (x2 - x1) * 1.2), max(128, (y2 - y1) * 1.2)
        bbox = [max(0, cx - hx) / im.width, max(0, cy - hy) / im.height,
                min(im.width, cx + hx) / im.width, min(im.height, cy + hy) / im.height]
        requests.append({'request': CropRequest(source, bbox, '区分独立痕迹、材料不连续与正常结构或反光'),
                         'priority': 4 if finding['certainty'] == 'uncertain' else 3, 'evidence_ids': [record['evidence_id']]})
    limited = {lookup[o['image_id']]['source'] for o in baseline['result']['observations'] if o['quality'] == 'limited'}
    for source, im in images.items():
        roi = subject_box(im); area = (roi[2] - roi[0]) * (roi[3] - roi[1]) / (im.width * im.height)
        if source in limited: triggers.append(source + ':limited_observation')
        if area < .3: triggers.append(source + ':small_subject')
        if roi != (0, 0, *im.size):
            requests.append({'request': CropRequest(source, [roi[0] / im.width, roi[1] / im.height, roi[2] / im.width, roi[3] / im.height], '保留主体上下文并检查可见表面'),
                             'priority': 2 if source in limited or area < .3 else 1, 'evidence_ids': []})
    # 同一原图高度重叠请求合并；各视角优先获得覆盖，文件名仅用于稳定打破平局。
    pending = sorted(requests, key=lambda r: (-r['priority'], r['request'].image_id, r['request'].bbox))
    merged = []
    for candidate in pending:
        req = candidate['request']; match = None
        for existing in merged:
            other = existing['request']
            if req.image_id == other.image_id and min(overlap_fraction(req.bbox, other.bbox), overlap_fraction(other.bbox, req.bbox)) >= .8:
                match = existing; break
        if match: match['evidence_ids'] = list(dict.fromkeys(match['evidence_ids'] + candidate['evidence_ids']))
        else: merged.append(candidate)
    chosen = []; covered = set()
    while merged and len(chosen) < max_crops:
        merged.sort(key=lambda r: (-r['priority'], r['request'].image_id in covered, r['request'].image_id, r['request'].bbox))
        candidate = merged.pop(0); chosen.append(candidate); covered.add(candidate['request'].image_id)
    selected_ids = {eid for r in chosen for eid in r['evidence_ids']}
    for record in records: record['zoom_status'] = 'selected' if record['evidence_id'] in selected_ids else 'skipped_budget'
    full_entries = []
    for source, im in images.items():
        full_entries.append({'image_id': source + '::full', 'source': source, 'source_size': list(im.size),
                             'crop_box': [0, 0, *im.size], 'data_url': encode_image(im)})
    entries = full_entries + [execute_zoom(path_map[r['request'].image_id], r['request']) for r in chosen]
    return {'entries': entries, 'evidence': records, 'triggers': sorted(set(triggers)),
            'requests': [{'image_id': r['request'].image_id, 'bbox': r['request'].bbox,
                          'question': r['request'].question, 'evidence_ids': r['evidence_ids']} for r in chosen],
            'skipped_requests': len(merged), 'max_crops': max_crops}
