"""生成含原图像素框和压缩预览的独立HTML报告，不调用模型。"""
import argparse
import base64
import html
import json
from collections import Counter
from io import BytesIO
from pathlib import Path
from imaging import discover, load_image
from inspect_images import validate_completed

STATE = {'present':'存在','not_observed':'未发现','uncertain':'不确定'}
CATEGORY = {'linear_trace':'线状痕迹','material_loss':'局部缺损'}


def build(input_root, summary_path, output):
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    records = summary['records']
    if len({r['item_id'] for r in records}) != len(records):
        raise ValueError('汇总中存在重复物体')
    groups = discover(input_root)
    sections = []
    counts = {category:Counter() for category in CATEGORY}
    for record in records:
        item = record['item_id']
        if item not in groups:
            raise ValueError('汇总引用了输入中不存在的物体')
        if record['status'] != 'completed':
            sections.append(f'<details class="group" data-failed="1"><summary>{html.escape(item)} · 执行失败</summary><p>{html.escape(record.get("error","未提供错误详情"))}</p></details>')
            continue
        validate_completed(record, groups[item])
        result = record['result']
        review_failed=record.get('refinement',{}).get('status')=='failed'
        review_notice=('<p class="notice">复查失败，保留初判；完整流程未完成：'+html.escape(record['refinement'].get('error','未知错误'))+'</p>') if review_failed else ''
        for category in CATEGORY:
            counts[category][result[category]] += 1
        uncertain = any(result[c]=='uncertain' for c in CATEGORY)
        present = any(result[c]=='present' for c in CATEGORY)
        badges = ' · '.join(f'{CATEGORY[c]}：{STATE[result[c]]}' for c in CATEGORY)
        figures = []
        for path in groups[item]:
            im = load_image(path)
            width, height = im.size
            im.thumbnail((900,900))
            data = BytesIO()
            im.save(data,format='JPEG',quality=85)
            uri = 'data:image/jpeg;base64,'+base64.b64encode(data.getvalue()).decode('ascii')
            boxes, descriptions = [], []
            evidence = [e for e in record['evidence'] if e['source']==path.name]
            for index, e in enumerate(evidence,1):
                x1,y1,x2,y2 = e['bbox_pixels']
                color = '#c9352c' if e['certainty']=='confirmed' else '#b76a00'
                boxes.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" fill="none" stroke="{color}" stroke-width="4"/>')
                descriptions.append(f'<li>{CATEGORY[e["category"]]} / {html.escape(e["certainty"])}：{html.escape(e["description"])}；原图框 {html.escape(str(e["bbox_pixels"]))}</li>')
            figures.append(f'<figure><div class="picture" style="aspect-ratio:{width}/{height}"><img loading="lazy" src="{uri}" width="{width}" height="{height}" alt="{html.escape(item+" / "+path.name,quote=True)}"><svg viewBox="0 0 {width} {height}" aria-hidden="true">{"".join(boxes)}</svg></div><figcaption>{html.escape(path.name)} · 原图 {width}×{height}</figcaption><ul>{"".join(descriptions) or "<li>此视角没有报告候选框；不代表整组一定无目标细节。</li>"}</ul></figure>')
        notes=''.join(f'<li>{html.escape(note)}</li>' for note in result['limitations'])
        flow=record.get('evidence_flow',{})
        if flow:
            # 只以转义后的数据展示审计信息，模型文字不能注入HTML或脚本。
            notes+='<li>证据阶段：'+html.escape(' → '.join(flow.get('semantic_stages',[])))+'；预算外请求：'+str(flow.get('skipped_requests',0))+'</li>'
            notes+=''.join('<li>状态合并 '+html.escape(c)+'：'+html.escape(json.dumps(d,ensure_ascii=False))+'</li>' for c,d in flow.get('resolutions',{}).items())
            notes+='<li>完整支持/反对证据及历史候选见该物体JSON中的 evidence_flow.ledger。</li>'
        sections.append(f'<details class="group" data-uncertain="{int(uncertain)}" data-present="{int(present)}" data-negative="{int(not uncertain and not present and not review_failed)}" data-failed="{int(review_failed)}"><summary>{html.escape(item)} · {badges}</summary>{review_notice}<p>模式 {html.escape(record["mode"])} / thinking {html.escape(record.get("thinking",record.get("metadata",{}).get("thinking","未记录")))}；累计处理耗时 {record["elapsed_seconds"]} 秒</p><ul>{notes}</ul><div class="gallery">{"".join(figures)}</div></details>')
    completed = sum(r['status']=='completed' and r.get('refinement',{}).get('status')!='failed' for r in records)
    rows=''.join(f'<tr><td>{CATEGORY[c]}</td><td>{counts[c]["present"]}</td><td>{counts[c]["not_observed"]}</td><td>{counts[c]["uncertain"]}</td></tr>' for c in CATEGORY)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>多视角表面细节识别报告</title>
<style>body{font:16px/1.6 system-ui;color:#1c2b3a;background:#f5f6f8;max-width:1400px;margin:30px auto;padding:0 24px}h1{font-size:28px}.notice{padding:18px;background:#fff6db;border-left:4px solid #b76a00}table{border-collapse:collapse;background:white;margin:20px 0}th,td{border:1px solid #d3d9e0;padding:8px 20px}.toolbar{position:sticky;top:0;z-index:2;background:#f5f6f8;padding:12px 0}select,button{font:inherit;padding:8px 14px}details{background:white;border:1px solid #d3d9e0;border-radius:8px;margin:12px 0;padding:16px}summary{font-weight:600;cursor:pointer}.group[hidden]{display:none}.gallery{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}figure{margin:0;min-width:0}.picture{position:relative;background:#eee}.picture img{display:block;width:100%;height:auto}.picture svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}figcaption{color:#526579;font-size:14px}li{overflow-wrap:anywhere}@media(max-width:700px){.gallery{grid-template-columns:1fr}body{padding:0 12px}}</style>
<h1>多视角表面细节识别报告</h1><div class="notice">本报告为模型判断，不是人工真值。红框为确认的模型证据，橙框为不确定候选；定位框近似，证据条数不等于唯一缺陷数。“未发现”仅限已提供照片的可观察区域。图片预览已压缩，识别与坐标基于原图；极浅痕迹请回到原始照片检查。</div>'''
    page+=f'<p>汇总包含 {len(records)} 组，执行完成 {completed} 组，失败 {len(records)-completed} 组。预期处理 {len(summary.get("expected_items",records))} 组。报告不包含未人工核对样本的准确率。</p><table><thead><tr><th>类别</th><th>存在</th><th>未发现</th><th>不确定</th></tr></thead><tbody>{rows}</tbody></table>'
    page+='''<div class="toolbar"><label>筛选 <select id="filter"><option value="all">全部物体</option><option value="uncertain">含不确定判断</option><option value="present">含已确认目标细节</option><option value="negative">两类均未发现</option><option value="failed">执行失败</option></select></label> <button id="expand">展开当前列表</button> <button id="collapse">收起</button> <span id="count"></span></div>'''+''.join(sections)+'''
<script>const groups=[...document.querySelectorAll('.group')];const filter=document.getElementById('filter');function apply(){let n=0;for(const g of groups){g.hidden=filter.value!=='all'&&g.dataset[filter.value]!=='1';if(!g.hidden)n++;}document.getElementById('count').textContent='显示 '+n+' 组';}filter.onchange=apply;document.getElementById('expand').onclick=()=>groups.filter(g=>!g.hidden).forEach(g=>g.open=true);document.getElementById('collapse').onclick=()=>groups.forEach(g=>g.open=false);apply();</script></html>'''
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(page,encoding='utf-8')
    return {'groups':len(records),'completed':completed,'html':str(output.resolve())}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--summary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.input,args.summary,args.output),ensure_ascii=False))
