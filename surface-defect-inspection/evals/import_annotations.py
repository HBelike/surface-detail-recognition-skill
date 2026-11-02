"""导入Label Studio矩形JSON；严格校验尺寸、方向和人工来源。"""
import argparse
import json
import math
from pathlib import Path


def import_annotations(export_path: Path, image_manifest: dict) -> list[dict]:
    tasks = json.loads(Path(export_path).read_text(encoding='utf-8-sig'))
    rows = []; images = image_manifest.get('images', {})
    if len({t['data']['image_id'] for t in tasks}) != len(tasks): raise ValueError('标注任务图像重复')
    for task in tasks:
        image_id = task['data']['image_id']
        if image_id not in images: raise ValueError('标注图像不在冻结清单内')
        info = images[image_id]; width, height = info['size']
        annotations = [a for a in task.get('annotations', []) if not a.get('was_cancelled')]
        if len(annotations) > 1: raise ValueError('多个标注版本须先人工裁决，不自动选最新或合并')
        for annotation in annotations:
            if annotation.get('ground_truth') is False and annotation.get('created_username') == 'model':
                raise ValueError('模型预测不能当作人工位置真值')
            for result in annotation.get('result', []):
                if result.get('type') != 'rectanglelabels': continue
                value = result['value']
                if result.get('image_rotation', 0) != 0 or value.get('rotation', 0) != 0:
                    raise ValueError('请对EXIF校正副本以零旋转重新标注；旋转框暂不支持')
                if [result['original_width'], result['original_height']] != [width, height]: raise ValueError('标注尺寸与原图清单不一致')
                names = value['rectanglelabels']
                if len(names) != 1 or names[0] not in ('linear_trace', 'material_loss', 'normal_structure', 'uncertain'):
                    raise ValueError('标注矩形类别非法')
                x, y, w, h = (value[k] for k in ('x', 'y', 'width', 'height'))
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (x, y, w, h)):
                    raise ValueError('标注坐标必须为有限数值')
                if not (x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 100 and y + h <= 100): raise ValueError('标注坐标越界')
                rows.append({'source_type': 'human', 'image_id': image_id, 'item_id': info['item_id'],
                             'source': info['source'], 'image_sha256': info['sha256'], 'category': names[0],
                             'bbox_pixels': [math.floor(x * width / 100), math.floor(y * height / 100),
                                             math.ceil((x + w) * width / 100), math.ceil((y + h) * height / 100)]})
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--export', type=Path, required=True); p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); args = p.parse_args()
    result = import_annotations(args.export, json.loads(args.manifest.read_text(encoding='utf-8-sig')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'source': 'human', 'annotations': result}, ensure_ascii=False, indent=2), encoding='utf-8')
