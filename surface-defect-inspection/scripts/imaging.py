"""只读原图、准备 API 图像及确定性的局部视图。"""
import base64
import math
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageChops, ImageFilter, ImageOps

SUPPORTED = {'.png', '.jpg', '.jpeg', '.webp'}


def discover(root: Path):
    if not root.is_dir():
        raise ValueError(f'图片目录不存在：{root}')
    groups = {}
    direct = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith('.'))
    if direct:
        groups[root.name] = direct
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and not folder.name.startswith(('.', '__')):
            paths = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith('.'))
            if paths:
                if folder.name in groups:
                    raise ValueError('根目录与子目录物体名称冲突，请使用每个物体独立子目录的布局')
                groups[folder.name] = paths
    if not groups:
        raise ValueError('目录中没有可用的图片分组')
    return groups


def load_image(path):
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert('RGB')


def subject_box(im):
    """背景近似均匀时估计主体范围；失败时退回整图。"""
    small = im.copy()
    small.thumbnail((320, 320))
    corners = [small.getpixel(p) for p in [(0, 0), (small.width-1, 0), (0, small.height-1), (small.width-1, small.height-1)]]
    bg = tuple(sorted(c[cidx] for c in corners)[2] for cidx in range(3))
    channels = ImageChops.difference(small, Image.new('RGB', small.size, bg)).split()
    diff = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    mask = diff.point(lambda v: 255 if v > 24 else 0).filter(ImageFilter.MedianFilter(5))
    box = mask.getbbox()
    if box is None:
        return (0, 0, im.width, im.height)
    sx, sy = im.width / small.width, im.height / small.height
    x1, y1, x2, y2 = box
    px, py = max(12, (x2-x1)*sx*.06), max(12, (y2-y1)*sy*.06)
    return (max(0, int(x1*sx-px)), max(0, int(y1*sy-py)), min(im.width, int(x2*sx+px)), min(im.height, int(y2*sy+py)))


def encode_image(im):
    buffer = BytesIO()
    im.save(buffer, format='JPEG', quality=95, subsampling=0)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def prepare_images(paths, mode):
    entries = []
    for path in paths:
        im = load_image(path)
        full = (0, 0, im.width, im.height)
        boxes = [('full', full)]
        if mode in ('crops','context'):
            roi = subject_box(im)
            if mode=='crops' or (roi[2]-roi[0])*(roi[3]-roi[1]) < im.width*im.height*.85:
                boxes.append(('roi', roi))
            # 对较大的主体使用重叠分块，保留全图以防主体估计漏掉细部。
            x1, y1, x2, y2 = roi
            if mode=='crops' and max(x2-x1, y2-y1) > 1000:
                mx, my = (x1+x2)//2, (y1+y2)//2
                dx, dy = int((x2-x1)*.08), int((y2-y1)*.08)
                for idx, tile in enumerate([(x1,y1,mx+dx,my+dy),(mx-dx,y1,x2,my+dy),(x1,my-dy,mx+dx,y2),(mx-dx,my-dy,x2,y2)]):
                    boxes.append((f'tile{idx+1}', tile))
        for kind, box in boxes:
            if box[0] >= box[2] or box[1] >= box[3]:
                continue
            entries.append({'image_id': f'{path.name}::{kind}', 'source': path.name,
                            'source_size': list(im.size), 'crop_box': list(box),
                            'data_url': encode_image(im.crop(box))})
    return entries


def map_box(bbox, entry):
    x1, y1, x2, y2 = entry['crop_box']
    return [math.floor(x1+bbox[0]*(x2-x1)), math.floor(y1+bbox[1]*(y2-y1)),
            math.ceil(x1+bbox[2]*(x2-x1)), math.ceil(y1+bbox[3]*(y2-y1))]
