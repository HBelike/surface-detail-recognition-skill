# 多视角表面细节识别 Skill

使用通用多模态模型，对同一物体的多角度照片识别两类细节：**线状痕迹**（划痕、擦痕）与**局部缺损**（崩缺、材料缺失）。

项目包含一个目录型 Agent Skill。入口是 [surface-defect-inspection/SKILL.md](surface-defect-inspection/SKILL.md)，执行工具、提示词、判定规则和依赖均随目录提供。程序在本地运行，通过 OpenAI 兼容 Chat Completions 连接执行者选择的模型服务。

## 快速开始

需要 Python 3.10+、可用的视觉模型服务及其 API 凭据。模型须支持图片输入和 JSON mode；本仓库不包含模型权重或任何个人 API Key。

```powershell
git clone https://github.com/HBelike/surface-detail-recognition-skill.git
cd surface-detail-recognition-skill/surface-defect-inspection
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt

$apiCredential = Read-Host "你自己的 API Key" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $apiCredential).Password
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_MODEL = "替换为你有权限使用的视觉模型ID"

.venv\Scripts\python.exe scripts/inspect_images.py --input "图片目录" --provider openai --doctor
.venv\Scripts\python.exe scripts/inspect_images.py --input "图片目录" --output "结果目录" --provider openai --workers 1 --timeout 180 --batch-deadline 7200 --item-deadline 900
.venv\Scripts\python.exe scripts/build_report.py --input "图片目录" --summary "结果目录/summary.json" --output "结果目录/report.html"
```

macOS/Linux 使用 `.venv/bin/python` 并在本地设置相同环境变量。`OPENAI_BASE_URL` 可设为自己的兼容网关或本机服务，例如 `http://127.0.0.1:8000/v1`。`--doctor` 只检查本地配置和图片，不发起网络请求。真实识别调用会使用执行者账户的额度。

输入按物体分组：

```text
images/
  item-a/
    view-01.png
    view-02.png
  item-b/
    view-01.png
```

输出包括逐物体 JSON、summary.json、report.md 与内嵌图片的 report.html。present、not_observed、uncertain 分别表示有确认的模型证据、可观察区域未发现、证据不足或有争议。定位框为模型近似框。

## 让本地 Agent 使用

将整个 `surface-defect-inspection` 文件夹放到目标 Agent 支持的 Skills 目录，按该 Agent 的发现规则加载。也可直接告诉具有本地文件与命令执行能力的 Agent：

> 读取 surface-defect-inspection/SKILL.md，使用我在环境中配置的 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL，以 --provider openai 处理指定图片目录，输出 JSON 和 HTML 报告，不展示密钥。

只安装该 Skill 文件夹；evaluation 目录是离线评测资料，不参与识别请求。

## 文档

- [使用说明](USAGE.md)：环境配置、Agent 加载、运行、输出、错误处理与其他供应商。
- [评测思路、过程与横向对比](EVALUATION_SUMMARY.md)：标签、重复实验、错误案例、耗时和局限。
- [准确率实测结果](ACCURACY_RESULTS.md)：逐轮、分类别、逐物体结果。
- [OpenAI 兼容接口与凭据隔离](PROTOCOL_COMPATIBILITY.md)。
- [第三方依赖与复用说明](surface-defect-inspection/references/third-party.md)。

## 已完成的模型对照

以下为2026-11-02的六组开发物体、每模型三轮结果；三模型共54个真实任务。

| 模型 | 整组两类一致 | 类别一致 |
|---|---:|---:|
| Qwen3.8-Max-0902 | 13/18（72.22%） | 31/36（86.11%） |
| Kimi K2.7 Code | 12/18（66.67%） | 30/36（83.33%） |
| DeepSeek Flash | 8/18（44.44%） | 23/36（63.89%） |

拒判、失败和缺失保留分母。三者均未达到自定开发门槛；原题没有公布官方合格线。这不是独立盲测或泛化保证。没有人工位置框，不报告定位准确率。完整工程复核仅保存3/36个终态；Kimi K3.1 Pro未真实测通。通用接口参数不同后的效果需要另测，不能直接套用上述专用配置成绩。

## 测试与离线计分

在仓库根目录、已安装依赖的环境中运行：

```shell
python -m unittest discover -s surface-defect-inspection/tests -v
python surface-defect-inspection/scripts/evaluate.py --labels evaluation/labels-development-v2.json --predictions evaluation/A/qwen/round-1/summary.json --output evaluation/local-check.json
```

面试官版本已通过86项离线回归及独立解压回归，其中6项使用隔离进程和本机HTTP夹具验证标准协议，不消耗个人模型额度。离线测试通过不等于识别准确率达标。

公开仓库保留源码、文档、人工参考标签和可复算JSON。原题图片、内嵌图片HTML和完整历史快照另行留档；输入图片由使用者提供。第三方源码保留原有许可证及固定来源记录。
