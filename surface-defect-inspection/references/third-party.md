# 第三方依赖与参考

本项目通过公开SDK/API调用模型，没有复制模型权重或引入完整Agent框架。

| 直接依赖 | 验证版本 | 安装元数据中的许可证 |
|---|---|---|
| Pillow | 12.3.0 | MIT-CMU |
| pydantic | 2.13.2 | MIT |
| openai Python SDK | 2.45.0 | Apache-2.0 |

requirements.txt固定直接依赖；requirements-lock.txt为独立验收环境的完整依赖快照，安装许可证和原始声明仍随各依赖分发。

参考资料：

- Agent Skills目录与入口规范：https://agentskills.io/specification
- DeepSeek模型、图片与JSON接口：https://api-docs.deepseek.com/quick_start/pricing/ 、https://api-docs.deepseek.com/guides/vision/ 、https://api-docs.deepseek.com/guides/json_mode/
- DeepSeek thinking开关：https://api-docs.deepseek.com/guides/thinking_mode/
- Qwen官方图像定位/放大思路调研：https://github.com/QwenLM/Qwen3-VL 、https://github.com/QwenLM/Qwen-Agent

面试官推荐通用OpenAI兼容入口，服务和凭据由执行者显式提供；供应商专用配置不自动切换。Qwen-Agent仅借鉴图像ID、区域框与核验问题的工具契约，本项目区域选择/裁剪逻辑自行适配，未复制其源码或模型专用像素预算，不引入Qwen框架依赖。

实际复用：evals/vendor/anthropic/aggregate_benchmark.py来自anthropics/skills的skill-creator目录，Apache-2.0，保留原文件与LICENSE.txt。固定提交、源码URL及SHA-256见同目录SOURCE.json；该许可证只针对所复用目录，不推定整个仓库均为相同许可。aggregate_results.py为本地薄适配：不展示单配置虚假差值，不把缺失用量写成完整费用。

正常参照机制借鉴Anomalib VLM-AD（Apache-2.0），未复制源码、不引入Torch/Lightning。标注使用Label Studio原生RectangleLabels及JSON导出约定（上游Apache-2.0），本项目仅提供配置与转换器，不分发完整标注平台。Label Studio可在开发环境单独安装，不是考官运行Skill的必需依赖。

参考固定源码：Qwen-Agent d04611b01cb113148462fdc7c3a0d048558b9c50；Anomalib VLM-AD 95fc9598a0099dcfcf20dd2f965fc36e9a3cea9c。原始调研与不采用重型依赖的取舍见提交包docs/open-source-research-2026-11-02.md。
