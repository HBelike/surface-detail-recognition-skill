# 使用说明：多视角表面细节识别 Skill

## 作用与交付结构

本Skill使用现有通用多模态模型，对同一物体的多角度照片识别两类细节：线状痕迹（划痕、擦痕）和局部缺损（崩缺、材料缺失）。

`surface-defect-inspection/SKILL.md`是供本地Agent加载的入口，包含YAML格式的name、description与执行规范；scripts目录是执行工具，references目录是判定规则与提示词。本说明供使用者安装和运行，准确率结果另见交付包根目录的ACCURACY_RESULTS.md。

## 面试官使用：标准OpenAI兼容入口

在本地配置自己的模型服务和凭据，脚本在面试官机器运行。本Skill不包含或自动借用候选人的Key。接口遵循OpenAI Chat Completions：POST /v1/chat/completions，使用messages中的text/image_url图片输入，要求所选模型支持图像理解和JSON mode。

先按下一节安装Python依赖。然后在Skill目录的PowerShell中配置：

```powershell
$apiCredential = Read-Host "面试官自己的 API Key" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $apiCredential).Password
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_MODEL = "替换为面试官可用的视觉模型ID"
.venv\Scripts\python.exe scripts/inspect_images.py --input "图片目录" --provider openai --doctor
.venv\Scripts\python.exe scripts/inspect_images.py --input "图片目录" --output "结果目录" --provider openai --workers 1 --timeout 180 --batch-deadline 7200 --item-deadline 900
.venv\Scripts\python.exe scripts/build_report.py --input "图片目录" --summary "结果目录/summary.json" --output "结果目录/report.html"
```

OPENAI_BASE_URL可以替换为面试官自己的兼容网关地址；本机模型服务例如`http://127.0.0.1:8000/v1`。模型权重/服务由使用方部署，本包不提供离线模型权重。无鉴权本机服务仍需按其说明为SDK配置非空占位值，程序不预置该值。

| 配置 | 含义 |
|---|---|
| OPENAI_API_KEY | 使用方自己的Key，仅从环境读取，不接受命令行Key参数，不写入结果 |
| OPENAI_BASE_URL | 服务根地址，通常以/v1结尾；默认官方https://api.openai.com/v1 |
| OPENAI_MODEL | 使用方的视觉模型ID，或使用--model显式提供；没有默认猜测型号 |
| OPENAI_TOKEN_LIMIT_FIELD | 可选，默认max_completion_tokens；旧网关可设为max_tokens |
| OPENAI_MAX_OUTPUT_TOKENS | 可选，默认6000，范围1至32768 |

--base-url和--model优先于相应环境变量。通用入口不传DeepSeek/Qwen/Kimi私有参数。只支持Responses API而不支持Chat Completions的服务不属于本入口的兼容范围；仅文本模型不适用本题。JSON mode只保证JSON格式，业务Schema、图像引用和定位框仍由本地程序严格校验。

让本地Agent执行时可以直接说明：

> 读取此Skill目录的SKILL.md，使用我在环境中配置的OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL，以--provider openai处理指定图片目录，生成JSON和HTML报告，不展示密钥。

供应商专用入口及之前的准确率对照保留在后续章节。通用协议入口已通过本机HTTP夹具测试，不把该测试称为新的云模型准确率结果。

## 1. 环境与依赖

需要Python 3.10+、网络和所选模型的API凭据，无需本地GPU。先进入surface-defect-inspection目录，推荐创建虚拟环境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

已有合适环境时可安装requirements.txt。下面的命令均从Skill目录运行；含空格或中文的路径保留引号。macOS/Linux将`.venv\Scripts\python.exe`替换为`.venv/bin/python`，并在本地配置相同环境变量。

## 2. 准备输入

推荐每个物体一个子目录，目录内为同一物体的多个视角：

```text
images/
  item-a/
    view-01.png
    view-02.png
  item-b/
    view-01.png
```

支持PNG、JPEG和WebP；也支持直接提供一个物体的图片目录。不同物体不要混放到同一组。

## 3. 可选：配置Qwen专用凭据

如需复现历史Qwen专用配置，可使用Qwen单次整图识别，型号为qwen3.8-max-0902。PowerShell中可隐藏输入并仅配置当前进程环境：

```powershell
$qwenCredential = Read-Host "Qwen API Key" -AsSecureString
$env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new('', $qwenCredential).Password
```

交付包不包含密钥。真实识别产生的API费用由相应账户承担。

## 4. 可选Qwen配置：检查输入并识别

先检查全部图片和本地配置：

```powershell
.venv\Scripts\python.exe scripts/inspect_images.py --input "图片目录" --provider qwen --doctor
```

doctor不调用网络，不验证模型权限、余额或API可用性。

执行全部视角的单次整图识别，再生成HTML报告：

```powershell
.venv\Scripts\python.exe scripts/inspect_images.py --input "图片目录" --output "结果目录" --provider qwen --workers 1 --timeout 180 --batch-deadline 7200 --item-deadline 900
.venv\Scripts\python.exe scripts/build_report.py --input "图片目录" --summary "结果目录/summary.json" --output "结果目录/report.html"
```

把“图片目录”和“结果目录”替换为实际路径。推荐命令显式选择Qwen；省略--provider时，脚本兼容默认值仍是DeepSeek。

## 5. 让本地Agent使用

将整个surface-defect-inspection目录放到目标Agent支持的Skills目录，按该Agent的发现规则加载。也可以对具有本地文件读取和命令执行能力的Agent直接说明：

> 读取surface-defect-inspection目录中的SKILL.md，使用我在环境中配置的OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL，以--provider openai处理指定图片目录并生成HTML报告。不要展示密钥。

Skill文件夹整体保留，确保scripts、references及依赖清单与SKILL.md在一起。交付包中的evaluation目录和准确率报告是离线验证资料，不需要安装到Agent的Skills目录。

## 6. 查看输出与处理失败

- 每个物体一个JSON；summary.json保存批次记录；report.md保存文字结果；report.html包含内嵌图片和原图坐标框。
- present表示有模型确认的可定位证据，not_observed表示在可观察区域未发现，uncertain表示证据不足或有争议。failed为执行状态。
- 定位框是近似框，未经人工位置标注验收。未发现仅限定于提供照片的可观察区域。
- 退出码0表示本次执行完成，1表示部分失败，2表示配置或输入错误；执行完成不等于识别正确。
- 缺少凭据时先配置对应环境变量；权限或余额问题以供应商控制台及真实API错误为准。
- 使用相同命令和输出目录可复用通过校验的成功缓存；--force会重新调用模型。不要让多个进程写同一输出目录。
- 可用--items item-a item-b选择分组；推荐命令使用--workers 1，脚本支持1至8。

## 7. 其他供应商与可选实验流程

| provider | 环境变量 | 当前默认型号 |
|---|---|---|
| openai | OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL | 使用方显式提供 |
| qwen | DASHSCOPE_API_KEY | qwen3.8-max-0902 |
| deepseek | DEEPSEEK_API_KEY | deepseek-flash |
| kimi | MOONSHOT_API_KEY | kimi-k2.7-code |

切换时显式修改--provider，凭据由对应环境变量提供。Kimi K3.1 Pro仅完成离线参数适配，尚未通过真实调用验证，不能将K2.7的成绩当作K3.1成绩。

可选完整证据流程入口为scripts/run_skill.py，先整图识别，再按需要放大及裁决。该路径尚未完成本轮多模型三轮复验，已观察到长耗时与拒判，暂不作为本次推荐路径；详细配置见SKILL.md。

## 8. 验证与能力边界

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

历史交付源码及独立环境通过80项离线测试；新增通用协议入口后86项回归测试通过，其中6项验证本机HTTP协议及配置隔离；不含新的云模型准确率实验。本次模型实测覆盖6组开发物体、每模型3轮，Qwen整组一致13/18；未达到自定开发门槛。没有独立验证集与人工位置框，不承诺全样本准确率或全部缺陷覆盖。评分资料不会被识别入口读取为答案。
