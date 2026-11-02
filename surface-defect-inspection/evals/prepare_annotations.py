"""为Label Studio生成EXIF校正副本、冻结哈希清单和原生任务JSON。"""
import argparse
import hashlib
import json
import sys
from urllib.parse import quote
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from imaging import discover, load_image


def prepare(input_root, output, items=None):
    groups = discover(input_root); selected = items or list(groups)
    if set(selected) - groups.keys(): raise ValueError('不存在指定物体')
    if len(selected) != len(set(selected)): raise ValueError('指定物体重复')
    images = {}; tasks = []
    for item in selected:
        for path in groups[item]:
            image_id = item + '/' + path.name
            # 保留原扩展名作为身份，避免同名不同格式互相覆盖。
            target = output / 'images' / item / (path.name + '.png')
            target.parent.mkdir(parents=True, exist_ok=True)
            im = load_image(path); im.save(target, format='PNG')
            images[image_id] = {'item_id': item, 'source': path.name, 'size': list(im.size),
                                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                'annotation_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                                'annotation_copy': target.relative_to(output).as_posix(), 'orientation': 'exif_transposed'}
            tasks.append({'data': {'image_id': image_id, 'image': '/data/local-files/?d=' + quote(target.relative_to(output).as_posix(),safe='/')}})
    output.mkdir(parents=True, exist_ok=True)
    for filename, value in [('image-manifest.json', {'images': images}), ('tasks.json', tasks)]:
        (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'images': len(images), 'items': len(selected)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--items', nargs='+'); a = p.parse_args()
    print(json.dumps(prepare(a.input.resolve(), a.output.resolve(), a.items), ensure_ascii=False))
