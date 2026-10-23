# 模型选择与独立提供商（0.2.5）

每个用途的下拉框直接列出当前 MaiBot 已配置的模型。选择后保存即可，不再需要新增
“继承条目”，也没有“沿用 SDK 角色”的选项。旧继承条目只作为升级兼容数据保留。
底层SDK默认角色仅用于旧配置和接口兼容，普通用户无需填写。

“模型提供商”页读取当前 MaiBot 的模型目录、提供商名称、模型标识和任务角色。
SDK 2.16 仅提供角色列表，完整目录由后端只读解析已核实实例布局的
`config/model_config.toml`；不修改它，不返回其中的 API Key。
无法定位标准实例布局时明确显示仅 SDK 角色可用，不猜测其他文件。

模型条目有两种来源：

- 直接选择 MaiBot：选择宿主已有模型。文本、视觉和 ASR 经真实 SDK 的 `model_name` 指定模型，
  保留宿主完整路由与凭据管理；直接音视频输入因当前 SDK 无原始视频接口，
  使用所选协议并在后端引用宿主对应提供商配置，凭据不复制到插件。
- 用户独立配置：填写显示名称、真实模型标识、完整 HTTPS Base URL、API Key 与输入能力。
  不需要改 MaiBot 的模型配置。允许配置多项，同一服务商的不同模型可分别添加。

五个角色独立选择：文本与规划、关键帧视觉、ASR 转录、直接音频理解、直接视频理解。
页面显示实际选中的宿主模型；无已配置 ASR、音频或视频模型时关闭。选择路由后，还须在“浏览与理解”启用对应
媒体处理开关。ASR 是转录，不能当作模型已经听过音乐；直接音频输入与转录分开显示。

密钥保存在插件后端 `model-secrets.json`（0600、父目录0700）。公开设置文件
`model-routing.json` 只保存模型与路由，不保存密钥。密钥留空保留旧值；更换服务地址时
必须重新填写密钥，且凭据与地址绑定，避免把原密钥发给新地址。前端响应与日志导出只含
是否已配置，绝不回显原值。请勿把密钥写进提示词、模型名称或 URL 查询参数。

支持的传输协议：

- `openai_compatible`：文本/图片通过 `/chat/completions`；ASR 通过
  multipart `/audio/transcriptions`；直接音频为 `input_audio`，视频为 `video_url`
  的 Base64 Data URL。音视频字段是部分兼容服务的扩展，普通文本模型不因此获得媒体能力。
- `gemini`：原生 `generateContent`、`inline_data` 与独立 system instruction；
  Base URL 应包含服务实际 API 前缀，例如 `https://generativelanguage.googleapis.com/v1beta`。
  此路由支持直接音频/视频理解，不把它伪装为 multipart ASR。

直接视频不会把 B 站 Cookie 或带签名的播放 URL发给模型提供商：后端合法下载可访问媒体，
FFmpeg 生成最多120秒、最多8MiB的片段后提交。默认90秒，画面压到480宽。
只有勾选“视频同时读取真实音轨”且声明音频能力时才合并可访问音轨；证据记录是否含音频。
这不是实时无限视频流，也不代表完整观看。音频转录/理解同样只处理有界片段。

“保存”只保存，“保存并检查”返回诊断任务 ID。连接检查独立于生活预算，不扣视频条数、
浏览时长、下载量或每日模型/Token额度；服务商仍可能产生费用。
文本使用最小请求，视觉使用合成图片，ASR/音频使用合成短音频，视频使用合成短视频，
无需先浏览视频。检查只证明对应接口能响应，不证明真实内容的理解质量。

成功显示请求往返延迟 `latency_ms`；失败显示 `error_code`、可获得的 `http_status` 和脱敏原因。
例如 `HTTP_401`、`HTTP_429`、`TIMEOUT`、`CONNECT_ERROR`、`TLS_ERROR`、`MODEL_DISABLED`、
`LOCAL_FFMPEG_MISSING`。宿主未提供 HTTP 状态时保留其错误码或显示 `SDK_ERROR`，不编造状态。
诊断请求最长等待30秒，任务排队时间与接口延迟分开。全部结果持久保存，可在暂停生活时执行。

模型选择与独立提供商都可用 TOML 输入，分别见 `model-routing.example.toml` 和
`provider.example.toml`。普通 `settings.toml` 包含各模块开关与预算，不包含密钥。
页面短API请求采用串行队列，避免触及当前宿主最多两个并发桥接请求的限制。

协议依据：

- [OpenAI 官方转录接口](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
- [阿里云兼容 Chat 接口及音视频扩展](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)
- [Gemini 官方视频输入文档](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding)

接口形状已按官方文档实现；某个账号、地区、模型是否有权使用，必须以实际连接测试为准。
