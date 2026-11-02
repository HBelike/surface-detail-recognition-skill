# 面试官版：OpenAI兼容协议与凭据交付核验

## 交付要求

提交包不包含候选人或用户的实际API Key，也不包含.env、私钥或本机账户配置。执行者通过环境变量提供自己的OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL。命令中不接受Key参数，凭据不进入JSON/HTML报告。

## 协议与实现

- 复用已锁定的OpenAI Python SDK，通过Chat Completions格式发送请求：POST /v1/chat/completions，Bearer鉴权，messages文本与image_url Base64图片，response_format为json_object。
- 通用入口为--provider openai，不发送thinking、enable_thinking、vl_high_resolution_images等供应商私有字段。
- --model、--base-url优先于相应环境变量；缺少模型或Key就明确失败，不自动借用其他供应商凭据。
- 默认max_completion_tokens=6000；兼容旧网关时显式设置OPENAI_TOKEN_LIMIT_FIELD=max_tokens。OPENAI_MAX_OUTPUT_TOKENS控制输出上限，参数进入缓存指纹。
- 代码本地运行，可连接远程兼容API或执行者已部署的本机模型服务。所选服务必须支持Chat Completions、图片输入及JSON mode；本包不包含离线模型权重。

调用链：inspect_images/run_skill → get_provider读取执行者配置 → prepare_images → vision_client/OpenAI SDK → 执行者服务 → 本地Pydantic、图像ID与坐标校验 → JSON与HTML输出。没有新增依赖。

## 验证

原80项测试加6项本地协议测试，共86项回归测试通过。新增测试在隔离子进程中运行，使用仅供测试的假凭据、ThreadingHTTPServer与临时图片；不调用供应商云API、不使用已有个人Key。验证实际HTTP路径、Bearer头、图片内容、标准参数、独立工作目录、两种CLI、缓存配置变更、缺少Key、doctor零请求及非法配置不回显。解压后的最终包另行复验，结果写入随包核验记录。

此前54个真实模型任务的准确率结果保持原样，不把本地协议夹具当作新模型准确率实验。模型账户权限、余额及外部服务可用性由使用方自己的账户决定。

## 官方依据

- [Chat Completions请求格式](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [图像输入格式](https://developers.openai.com/api/docs/guides/images-vision?api-mode=chat)
- [JSON mode与本地Schema校验的边界](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
