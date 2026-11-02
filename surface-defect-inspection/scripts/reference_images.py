"""人工正常参照的可选薄层；按物体身份、原文件与规范像素哈希隔离。"""
import hashlib
import json
from pathlib import Path
from imaging import load_image, encode_image


def pixel_hash(path):
    image = load_image(path)
    return hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()


def load_references(category, excluded_item_ids, excluded_hashes=None, *, manifest_path=None, excluded_pixel_hashes=None):
    if manifest_path is None: return []
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if data.get('source') != 'human': raise ValueError('正常参照必须由人工确认')
    references = []; seen = set()
    for record in data.get('references', []):
        if record.get('category') != category: continue
        if record.get('confirmed_normal') is not True: raise ValueError('参照缺少人工正常确认')
        item = record.get('item_id')
        if not item: raise ValueError('正常参照缺少物体身份')
        if item in excluded_item_ids: raise ValueError('正常参照物体与评分物体重合')
        path = (manifest_path.parent / record['path']).resolve()
        if not path.is_relative_to(manifest_path.parent.resolve()): raise ValueError('参照路径必须位于清单目录内')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        pixels = pixel_hash(path)
        if digest != record.get('sha256'): raise ValueError('参照文件哈希不匹配')
        if digest in (excluded_hashes or set()) or pixels in (excluded_pixel_hashes or set()):
            raise ValueError('正常参照图像与评分数据重复')
        if pixels in seen: continue
        seen.add(pixels); im = load_image(path)
        references.append({'image_id': 'normal-reference-' + digest[:16], 'role': 'normal_reference',
                           'category': category, 'reference_item_id': item, 'source': path.name,
                           'sha256': digest, 'pixel_sha256': pixels, 'source_size': list(im.size), 'data_url': encode_image(im)})
    return references[:2]


def configure_references(manifest_path, groups):
    """一次隔离整个评分批次，不能只排除当前物体。"""
    if manifest_path is None: return {}, {}
    data = json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    categories = data.get('item_categories', {})
    file_hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for paths in groups.values() for p in paths}
    pixels = {pixel_hash(p) for paths in groups.values() for p in paths}
    contexts = {}; snapshot = {}
    for item in groups:
        category = categories.get(item)
        if not category:
            contexts[item] = []; snapshot[item] = {'status': 'zero_shot_no_category', 'references': []}; continue
        refs = load_references(category, set(groups), file_hashes, manifest_path=manifest_path, excluded_pixel_hashes=pixels)
        contexts[item] = refs
        snapshot[item] = {'status': 'reference_conditioned' if refs else 'zero_shot_no_qualified_reference',
                          'references': [{k: v for k, v in r.items() if k != 'data_url'} for r in refs]}
    return contexts, snapshot
