# 工程评测与位置标注

所有命令从Skill目录执行。评测脚本只在计分时读取人工标签，识别脚本不读取evals/cases.json或人工答案。

## 复用已有结果计分

```powershell
python -X utf8 evals/adapt_runs.py --summary "历史结果/summary.json" --labels "人工标签.json" --output "benchmark/eval-0/old_skill/run-1"
python -X utf8 evals/aggregate_results.py "benchmark"
```

每轮生成exact-score.json、grading.json、timing.json，再复用Anthropic聚合脚本。缺少完整用量或墙钟耗时不会用字符数/零冒充。只有旧配置时不输出改进幅度。重新计分不等于发起新推理。

## 冻结工程A/B

```powershell
python -X utf8 evals/run_benchmark.py --old-zip "旧正式提交包.zip" --input "图片目录" --labels "人工标签.json" --output "全新A-B目录"
```

该命令只冻结旧包、新代码、图片哈希、标签和交错三轮日程，不调用模型。旧包SHA必须等于已经验收的版本。真正执行时，在已有环境变量中配置凭据，再运行：

```powershell
python -X utf8 evals/run_benchmark.py --output "全新A-B目录" --execute
```

旧新各三轮，单次输出独立目录且--force。供应商上下文缓存不可控，响应ID和返回版本以原始结果为准。固定六组门槛：新版每轮至少5组两类都正确、三轮合计至少15/18、三轮都正确至少4/6；与对应旧轮相比不新增阳性漏检。拒判、失败与缺失不能排出分母。不通过不替换正式包，不挑最好轮次；通过也不能证明未知样本同等准确。

## Label Studio位置标注

```powershell
python -X utf8 evals/prepare_annotations.py --input "图片目录" --output "标注工作目录" --items item-a item-b
```

输出EXIF校正PNG副本、tasks.json和image-manifest.json。单独启动已安装的Label Studio时，将LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED设为true，LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT设为“标注工作目录”的绝对路径；导入label_studio_config.xml配置及tasks.json。只对可见证据画矩形，正常结构误报区域可标normal_structure，模糊处标uncertain。不要通过模型结果预填人工真值。

从Label Studio导出原生JSON后：

```powershell
python -X utf8 evals/import_annotations.py --export "导出.json" --manifest "标注工作目录/image-manifest.json" --output "人工位置.json"
python -X utf8 evals/localization.py --summary "结果目录/summary.json" --annotations "人工位置.json" --output "位置评估.json"
```

转换百分比xywh为原图xyxy，拒绝越界、尺寸不符、多版本未裁决以及非零旋转。位置指标仅覆盖已人工画框的区域，同图同类匹配；细线IoU对框宽敏感，须结合区域命中和人工核对。没有人工框就没有定位准确率。

## 独立正常参照

normal_references.json默认空库，item_categories为物体ID到人工品类的映射；references中的每项须有category、item_id、相对path、文件sha256和confirmed_normal=true。path位于清单目录内。添加真实正常图后：

```powershell
python scripts/run_skill.py --input "图片目录" --output "结果目录" --references "正常参照清单.json"
```

同品类、不同物体，并与整个评分输入目录按身份、文件和规范像素哈希隔离。模型确认不能替代人工正常确认，缺少品类或合格参照时继续零样本。当前仅DeepSeek适配接入正常参照。
