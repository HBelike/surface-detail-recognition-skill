"""生成不含模型答案的人工核对页。"""
import argparse
import csv
import html
import json
import os
from pathlib import Path
from imaging import discover, load_image

DEV_ITEMS = ['item-p01', 'item-p08', 'item-p10', 'item-p14', 'item-p17', 'item-p25']


def prepare(root, output, items):
    groups = discover(root)
    missing = set(items) - groups.keys()
    if missing:
        raise ValueError(f'不存在的物体：{sorted(missing)}')
    output.mkdir(parents=True, exist_ok=True)
    sections, manifest = [], []
    for item in items:
        pictures = []
        for path in groups[item]:
            im = load_image(path)
            # 相对路径保证同一交付目录移动后仍能打开。
            src = html.escape(Path(os.path.relpath(path, output)).as_posix(), quote=True)
            pictures.append(f'<figure><a href="{src}" target="_blank"><img loading="lazy" src="{src}" alt="{html.escape(path.name)}"></a><figcaption>{html.escape(path.name)} · {im.width}×{im.height}</figcaption></figure>')
        def select(category, label):
            return f'<label>{label}<select data-category="{category}"><option value="">请选择</option><option value="present">有明确证据</option><option value="not_observed">未发现</option><option value="uncertain">不确定／证据不足</option></select></label>'
        sections.append(f'<section data-item="{html.escape(item)}"><h2>{html.escape(item)}</h2><div class="gallery">{"".join(pictures)}</div><div class="fields">{select("linear_trace","线状痕迹")}{select("material_loss","局部缺损")}</div><label>证据或疑问（写图片名、部位）<textarea placeholder="例如：view-02.png 左侧边缘；看不清则说明原因"></textarea></label></section>')
        manifest.append({'item_id': item, 'images': [p.name for p in groups[item]], 'split': 'development'})
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>表面细节人工核对</title>
<style>body{font:16px/1.6 system-ui;margin:32px auto;max-width:1280px;padding:0 20px;background:#f4f5f7;color:#182331}h1{font-size:28px}section{background:white;padding:24px;margin:24px 0;border:1px solid #d6dce3;border-radius:12px}.gallery{display:flex;flex-wrap:wrap;gap:12px}figure{margin:0;width:calc(33.333% - 12px)}img{width:100%;height:300px;object-fit:contain;background:#eee}figcaption{font-size:13px;color:#536276}.fields{display:flex;gap:30px;flex-wrap:wrap;margin:16px 0}label{display:block}select{display:block;padding:10px;min-width:220px}textarea{box-sizing:border-box;width:100%;min-height:70px;padding:10px}button{padding:12px 20px;background:#173e61;color:white;border:0;border-radius:6px;cursor:pointer}.toolbar{position:sticky;bottom:0;background:#f4f5f7;padding:14px;border-top:1px solid #ccc}@media(max-width:700px){figure{width:100%}}</style>
<h1>人工核对 · 先独立判断，再对照模型</h1><p>本页不包含模型判断。点击图片打开原图，放大检查。明确划痕/擦痕计入线状痕迹；明确崩缺/断口/材料缺失计入局部缺损；正常孔洞、锯齿、螺纹、接缝排除。加工纹/反光有争议或图片看不清时选择不确定。“未发现”只限于本组照片，不代表未拍摄表面没有缺陷。</p><p>两类可同时存在。标记“有明确证据”时请在备注写出图片名和部位。导出后将 JSON 文件交给 Agent；刷新前请导出保存。</p>''' + ''.join(sections) + '''<div class="toolbar"><button id="export">导出人工标签 JSON</button><span id="status" role="status"></span></div>
<script>document.getElementById('export').onclick=()=>{const labels=[...document.querySelectorAll('section[data-item]')].map(s=>({item_id:s.dataset.item,linear_trace:s.querySelector('[data-category="linear_trace"]').value,material_loss:s.querySelector('[data-category="material_loss"]').value,note:s.querySelector('textarea').value,reviewed:true}));const bad=labels.filter(x=>!x.linear_trace||!x.material_loss||((x.linear_trace==='present'||x.material_loss==='present')&&!x.note.trim()));if(bad.length){document.getElementById('status').textContent=' 请补全两类判断及阳性证据：'+bad.map(x=>x.item_id).join('、');return;}const data={schema_version:'1.0',source:'human',reviewed_at:new Date().toISOString(),labels};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='labels-development.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);document.getElementById('status').textContent=' 已导出。请保留原图与备注。';};</script></html>'''
    (output / 'review.html').write_text(page, encoding='utf-8')
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'labels-template.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['item_id', 'linear_trace', 'material_loss', 'note', 'reviewed'])
        writer.writerows([item, '', '', '', 'false'] for item in items)
    print(json.dumps({'review': str((output/'review.html').resolve()), 'groups': len(items), 'images': sum(len(groups[i]) for i in items)}, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--items', nargs='+', default=DEV_ITEMS)
    a = p.parse_args()
    prepare(a.input.resolve(), a.output.resolve(), a.items)
