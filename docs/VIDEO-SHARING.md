# 分享真实视频到聊天

视频分享独立于 B站评论/回复，默认关闭。主聊天可以在被主动敲门等插件唤醒后自主决定是否分享；本功能不新增聊天定时器，不修改宿主人格。

## 配置

在“设置总览 → 视频分享”填写：

```toml
[sharing]
video_share_enabled = true
video_share_stream_id = "YOUR_TARGET_SESSION_ID"
video_share_format = "qq_ark"
video_share_daily_limit = 2
video_share_cooldown_seconds = 10800
```

目标会话需已在宿主加载，QQ 大图卡片要求目标平台为 QQ。`text` 是显式选择的文字模式，不是卡片失败后的自动回退。活跃时间和休息仍遵循现有设置；每日上限包含结果未知的尝试。

QQ 适配器需支持 SDK 的 `send.custom("qq_ark", payload, stream_id, return_details=True)`。仅支持文本/图片的旧适配器会拒绝此类型；需先确认适配器实现、卡片权限和 URL 配置。无需更改 MaiBot 主程序或本插件的 SDK 版本要求。

## 使用

主聊天先调用 `bililife_share_candidates` 查看未分享到当前目标的真实见闻、覆盖范围和来源，再自主决定是否分享。发送调用 `bililife_share_video(video=...)`，参数为候选的 `video_id` 或记忆检索中的 B站 `source` URL。工具只允许在管理员指定的目标会话使用，不接受任意发送对象。个人看法由正常聊天表达，成功后不要再发重复链接。主模型可以不分享，主动任务被接受不等于消息发送成功。

WebUI“浏览与理解 → 视频经历 → 详情”可显式分享到指定会话；“视频分享记录”显示结果和平台消息 ID。

## 回执与去重

只分享已理解并存在活跃记忆的视频。同一视频/目标在网络请求前通过 SQLite 事务唯一认领，并发或重启后不重复发送。旧经历缺封面时刷新视频元数据，不重新观看。封面使用公开 HTTPS 地址，卡片不含 Cookie 或模型密钥。

收到成功回执后记录 `sent` 和消息 ID。超时、异常或没有成功回执时记为 `unknown`；启动时将中断的 `sending` 转成 `unknown`。它不等于“未发送”，不会自动重发或降级成文字，也不代表用户已读；应先核对目标聊天再人工处理。

使用腾讯 [Ark 37 大图模板](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/type/template/template_37.md)，提供标题、UP 主、封面和跳转链接。它不是 B站原生分享协议，不保证 QQ 内直接播放；消息权限与主动发送限制仍由平台控制。

## 验证

离线测试覆盖卡片请求、真实经历和固定目标要求、关闭/暂停/休息、重复及并发认领、重启恢复、冷却/每日上限、无效封面，以及不确定回执不自动重发或回退。原作者服务器源码缺失导致的历史测试跳过需单独记录。
