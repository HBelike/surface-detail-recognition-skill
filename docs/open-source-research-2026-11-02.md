# 开源调研：多视角表面细节识别改造

调研日期：2026-11-02。范围：GitHub实际源码、官方文档、skills.sh/Skills CLI。外部SKILL.md均作为待审资料读取，没有把其中指令当作本任务授权，没有自动安装外部Skill。现有提交包和生产识别代码未修改。

## 结论

本次检索没有找到经过核验、可直接替换当前实现并保证完成“两类细节、多视角、定位、DeepSeek、本地Agent执行”的现成Skill。找到的可复用组合是：

1. Qwen-Agent的区域放大工具接口与裁剪边界处理。
2. Anthropic skill-creator的旧版/新版对照、盲评、重复结果聚合脚本。
3. Anomalib VLM-AD的正常参考图机制。
4. Label Studio的位置标注与标准导出。
5. Promptfoo的Python provider/assertion及重复评测，作为运行环境满足后的备选。

这是一组适配选择，不是这些项目已在本题上提高准确率的证据。

## 项目、许可与维护核验

仓库状态来自GitHub API当次查询，保存在research/2026-11-02-open-source/repositories.json；源码路径、具体文件提交SHA和日期见pinned-sources.json。pushed_at只代表仓库推送记录，不保证某模块最近维护或质量。

| 项目 | 许可核验 | 当次仓库最近推送UTC | 决策 |
|---|---|---|---|
| QwenLM/Qwen-Agent | Apache-2.0，工具文件有许可头 | 2026-03-04 | 选用工具契约/局部代码；不引入完整框架。目标工具文件最近修改为2025-09-23，不能当作持续活跃更新的证据 |
| anthropics/skills 的 skill-creator目录 | 目录LICENSE.txt为Apache-2.0；仓库API没有统一许可值，不能扩大到全部Skill | 2026-10-28 | 优先复用聚合脚本与对照流程 |
| promptfoo/promptfoo | MIT，已读LICENSE | 2026-11-02 | 功能合适，但当前package.json为0.125.1、Node要求>=22.22.0；本机22.18.0，暂不增加环境迁移 |
| HumanSignal/label-studio | Apache-2.0，已读LICENSE | 2026-11-02 | 作为开发期位置标注工具，不成为考官运行依赖 |
| open-edge-platform/anomalib | Apache-2.0，源码有许可头 | 2026-11-02 | 借鉴VLM-AD正常参照；不直接替换当前两类结构化识别 |
| Mindrally/skills/computer-vision-opencv | Apache-2.0，仓库元数据核验 | 2026-10-08 | Skills CLI显示约3.9K安装，仓库约289星；实际SKILL只有通用OpenCV/PyTorch建议，不是可验证的缺陷识别实现，不安装 |

K-Dense-AI项目已重定向为scientific-agent-skills；本次尝试的旧scikit-image Skill路径为404，当前树搜索未找到同名路径，因此不列为已验证推荐。

## 1. Qwen-Agent：主动请求查看区域，而非机械放大前三张

核实源码：[ImageZoomInToolQwen3VL](https://github.com/QwenLM/Qwen-Agent/blob/d04611b01cb113148462fdc7c3a0d048558b9c50/qwen_agent/tools/image_zoom_in_qwen3vl.py)。

实际提供img_idx、bbox_2d、label工具参数，指定具体输入图与区域，完成坐标换算、边界处理、裁剪和图像返回。可借鉴其“模型提出区域请求→确定性工具执行→再观察”的契约，取代按文件名排序、只取前三个视角和首个候选的机械选择。

适配边界：

- Qwen源码坐标范围0–1000，当前项目0–1；只在适配边界转换，内部继续保持现有约定。
- factor=32、smart_resize和像素预算针对Qwen，不直接套用到DeepSeek。
- 当前DeepSeek视觉输入会按自身规则缩放，高分辨率上传和插值放大不等于获得新细节：[官方Vision说明](https://api-docs.deepseek.com/guides/vision/)。
- 保留原图上下文、EXIF映射、正面积与路径检查；不照搬远程URL下载分支。若复制局部代码，保留Apache许可头、版权和变更说明。
- 这一工具能改善观察过程，不能单独区分正常加工纹与真实划痕。

## 2. Anthropic skill-creator：直接复用评测基础设施

已阅读：[SKILL.md](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md)、[聚合脚本](https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/aggregate_benchmark.py)、[盲评流程](https://github.com/anthropics/skills/blob/main/skills/skill-creator/agents/comparator.md)、[目录许可](https://github.com/anthropics/skills/blob/main/skills/skill-creator/LICENSE.txt)。

可复用：旧版/新版在同一输入上对照、每项判断附证据、重复运行、均值/标准差/范围、耗时与tokens、匿名A/B查看。人工真值上的类别和整组一致性必须用确定性代码计分，不使用另一个LLM作为真值。

已执行最小可行性验证：把已有三轮旧版真实结果适配为eval-0/old_skill/run-N/grading.json、timing.json，直接执行上游aggregate_benchmark.py；输出整组通过率均值0.6667、轮间标准差0。结果位于research/2026-11-02-open-source/reuse-proof/benchmark.json。没有运行或虚构新版分数；只有一个配置时上游delta不具备A/B改进含义，必须忽略。

还需注意上游在缺少tokens时存在output_chars回退；适配器必须提供真实usage，不能把字符数冒充模型tokens。运行耗时也需统一口径。

## 3. Anomalib VLM-AD：正常参照有用，默认二分类解析不合适

已读：[官方模型说明](https://anomalib.readthedocs.io/en/latest/markdown/guides/reference/models/image/vlm_ad.html)、[具体实现](https://github.com/open-edge-platform/anomalib/blob/95fc9598a0099dcfcf20dd2f965fc36e9a3cea9c/src/anomalib/models/image/vlm_ad/lightning_model.py)。

VLM-AD支持零样本和k张正常参考图，不需要为此训练一个专用检测器。对本题可借鉴：若有经确认、同品类、不同物体的正常参考图，把正常结构、加工纹与目标局部并排比较，降低仅凭“亮线”判划痕的风险。

不能直接照搬：源码默认用回答是否以Y开头解析异常标签，是通用正常/异常二分类，不提供本题两类细节、三态、位置框及多视角证据约束；后端枚举也没有现成DeepSeek适配。直接引入还会带来Torch/Lightning等依赖。

参照数据必须与评分样本按物体身份、原图哈希隔离；禁止将p08测试图片当正常范例又统计p08测试成绩。当前缺少独立正常参照时保持零样本，不从未标注图片中猜正常、不生成伪真值。当前多视角无配准，不用SSIM或简单像素差替代缺陷判定。

## 4. Label Studio：使用成熟位置标注，停止自建画框界面

参考：[RectangleLabels](https://labelstud.io/tags/rectanglelabels)、[导出说明](https://labelstud.io/guide/export.html)。

复用其矩形标注与JSON/COCO导出；先给代表性阳性和p08误报区域补人工位置核对，不需要把135张图全部精标。按物体、图像、类别、原图尺寸保存位置与人工确认状态。

导出的x/y/width/height为0–100百分比，并有original_width、original_height、image_rotation字段；导入时转换到当前EXIF校正后原图坐标，先处理旋转，避免正确框被错误换算。旋转非零时不得按未旋转框直接算IoU。细长划痕的矩形IoU对框宽敏感，应同时报告原图上的区域命中和人工复核，不以一个IoU阈值概括定位质量。

Label Studio仅作为开发期工具，不引入考官Skill运行环境。

## 5. Promptfoo：后续统一运行器，当前先避免Node迁移

可复用[Python provider](https://www.promptfoo.dev/docs/providers/python/)、[Python assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/python/)、[重复与无缓存评测](https://www.promptfoo.dev/docs/usage/command-line/)。

可用call_api薄适配现有run_skill.py，用get_assert按人工JSON严格计分；--repeat 3 --no-cache -j 3控制重复与并发。注意--no-cache只禁用Promptfoo层缓存；每次子进程还必须使用独立输出目录或显式force，避免当前Skill缓存掩盖波动。标签不得传入模型输入。

当前仓库Node门槛高于本机；今天优先用Anthropic聚合脚本。后续若使用Promptfoo，应固定已发布版本并先在隔离Node环境核验，不升级全局环境、不自动开启云分享。两个运行器不同时引入，以免维护两套评测口径。

## 需要自行实现的最小领域逻辑

没有核实到现成项目可以直接正确裁决本题的跨阶段矛盾。必须保留薄的领域层：证据ID、视角与原图区域、支持/反对/不足、同一原图派生图不能算独立投票、覆盖不到旧区域的“未见”不能推翻旧阳性；冲突最多一次针对性裁决，仍不清楚保留uncertain。

这部分直接服务本题，不能用多数投票、模型自报confidence或外部框架替代证据规则。最终是否改善，需要与现有3轮旧版基准对照，不能由本调研推定。
