# MaiBot BiliLife / 麦麦的 B 站生活 · 0.2.5

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

让麦麦按兴趣制定日程、阅读与理解 B 站视频、整理评论和回复、保存带来源的经历。
面向已验证的 **MaiBot 1.3.14 / SDK 2.16.0 / dashboard 1.9.8 / Python 3.12**。
没有升级宿主。

## 0.2.5 的使用方式

- **每日安排**：24小时钟面与彩环，旁边列出任务。可切换日期、重新生成今日计划、插入指定时段、删除和恢复显示。
- **浏览与理解**：每日浏览量、总时长、主题/UP 主/BV 范围及字幕、关键帧、ASR、音视频开关。
- **评论与回复**：分别设置每日评论量、回复量、冷却、检查频率和自动发布开关，不再使用运行模式。
- **兴趣与人格**：手动新增兴趣、调整权重、冻结和回滚；填写人工偏好与表达说明，保留版本。
- **模型**：每个用途直接选择 MaiBot 已配置模型，也可单独配置提供商。保存与连接检查分开，结果放在对应用途下。
- **设置总览**：账号和各模块概况，跳转到相应模块；提供整份 TOML 编辑和导出。

九个页面使用宿主真实的 `--maibot-*` 主题变量与组件样式，会跟随明暗和 dashboard 风格。
不依赖 CDN、独立 Web 服务或浏览器自动化。

## 配置与默认权限

业务配置是插件私有目录的 **`settings.toml`**。常用项可在模块表单调整；
需要手填或批量修改时用 TOML 编辑器。模板见 [settings.example.toml](settings.example.toml)。

```toml
[browse]
browsing_enabled = true
max_videos = 4
max_browse_seconds = 2400

[comments]
comments_enabled = true
auto_publish_comments = false
max_comments = 1

[replies]
replies_enabled = true
auto_publish_replies = false
max_replies = 2

[persona]
evolution_enabled = true
persona_guidance = "这周多了解天文，遇到争论就结束。"
expression_preferences = ["表达简短", "保留不确定性"]
```

页面保存立即生效，同时更新 SQLite 配置快照和 TOML。直接修改服务器文件后重载插件。
非法 TOML 或越界值不会覆盖上次有效设置，总览会显示具体配置错误状态。
宿主 `config.toml` 仍只管理插件启用和 SDK 配置格式版本。

默认生成草稿，**不自动发布评论或回复**。启用某个自动发布开关后，仍受内容范围、
上下文、额度、间隔、重复检测和账号状态检查。额度是上限，不是必须完成的指标。
“暂停全部任务”和“停止自动发布”始终可见；已经发出的请求不能假装撤回。
从0.2.0升级时将旧权限转换为各模块设置，不因移除模式而开启发布。

账号在设置总览独立扫码。Cookie 保存于后端 `credential.json`；模型密钥在
`model-secrets.json`，文件0600、目录0700，不进入前端回显、普通TOML或日志导出。
模型选择 TOML 见 [model-routing.example.toml](model-routing.example.toml)，
独立模型输入格式见 [provider.example.toml](provider.example.toml)。

## 真实证据、互动与记忆

视频资料区分元数据、带时间戳字幕、ASR、抽样帧与直接音视频片段。
只有标题简介时标记“元数据浏览”，禁止据此发布；抽样不会被称为完整观看。
下载、转码和模型调用有时长、大小、次数和存储预算；FFmpeg 使用异步参数数组。
媒体连接拒绝任意域名、内网 DNS 地址和重定向，外部文字没有系统指令权限。

发布先持久登记 outbox。请求超时、进程中断或结果未知时禁止盲目重发；只读核对能唯一
匹配时补记成功，否则保持未知。不承诺网络发布 exactly-once。
回复只跟进本账号已发布评论的相关线程，保存根评论、父评论和被回复对象。

`bililife_recall` 是实际注册的宿主检索工具，按相关性、时间、重要度返回有界经历和来源。
可解析真实会话时调用 `maisaka.context.append`。插件经历、可检索记忆、上下文追加与
宿主长期写入分别显示；当前宿主无已核实的长期记忆写入接口，显示不支持。
人格干预是插件偏好，宿主核心人格和权限不会被自动改写。

## 持久化与开发

SQLite WAL、迁移版本2、同日有效计划唯一索引、事务领取和本机文件锁保护调度。
删除日程采用隐藏与取消，保留审计和发布记录；恢复显示不会重新执行已经取消的任务。
重新生成计划会取消旧计划未完成任务，保留手工独立安排和已执行记录。
任务和截止时间持久保存，卸载释放客户端、子进程和执行锁。

```powershell
python -m pip install -r requirements-test.lock
python -m pytest tests -q
python scripts/build_webui.py
python scripts/package.py
```

宿主支持的安装包为 `dist/maibot-bililife-0.2.5.zip`，附SHA256。
源文件在 `webui/src/`，构建产物为无外部资源的 `webui/index.html`。

- [安装、备份、升级与回滚](docs/OPERATIONS.md)
- [模型提供商](docs/MODEL_PROVIDERS.md)
- [0.2.5验收与边界](docs/ACCEPTANCE_0.2.5.md)
- [实际兼容环境](docs/COMPATIBILITY.md)
- [架构](docs/ARCHITECTURE.md) · [第三方许可与清单链接](THIRD_PARTY.md)

## 安装包与许可证

从本仓库 [Releases](https://github.com/Sakyurara/maibot-bililife/releases) 下载版本化
安装 ZIP，并核对同页 SHA256 文件。GitHub 自动生成的源码 ZIP 不替代插件安装包。

本项目采用 [MIT License](LICENSE)，Copyright (c) 2026 Sakyurara and BiliLife contributors。
复用平台客户端的原作者版权与许可见 [第三方说明](THIRD_PARTY.md) 和 `vendor/LICENSE`。
