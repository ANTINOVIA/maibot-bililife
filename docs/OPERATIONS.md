# 安装、配置、更新与卸载

## 环境与目录

宿主兼容范围为 MaiBot 1.3.14–1.3.99，SDK 限定为 2.16.0。实际验证的宿主环境为 MaiBot 1.3.14、SDK 2.16.0、Dashboard 1.9.8 和 Python 3.12；范围内其他补丁版本尚未逐一验证。

下表以宿主根目录 `/opt/maibot` 为例，其他实例以实际配置为准：

| 用途 | 路径 |
| --- | --- |
| 插件源码 | `/opt/maibot/plugins/antinova_maibot-bililife` |
| 专属配置与数据 | `/opt/maibot/data/plugins/antinova.maibot-bililife` |
| 临时媒体 | `/opt/maibot/temp/plugins/antinova.maibot-bililife` |
| 插件页面 | `/extensions/antinova.maibot-bililife/overview` |

页面使用宿主 WebUI v2 桥接，不需要单独的公网服务。`scripts/` 提供 WebUI 构建、安装包生成和只读预检工具。

## 安装

1. 从 [Releases](https://github.com/ANTINOVIA/maibot-bililife/releases/tag/v0.2.6) 下载 `antinova.maibot-bililife-0.2.6.zip`，核对同名 SHA256 文件。
2. 在 MaiBot 插件管理界面导入安装包并启用，打开插件设置总览，独立扫码登录 B 站。
3. 在模型页选择宿主已有模型，或配置独立提供商。模型输入能力须与理解方式匹配；没有可用 ASR 时关闭 ASR。
4. 配置主题、日程与每日额度。评论和回复自动发布默认关闭，需要时分别开启。

关键帧、音频和视频片段处理需要 FFmpeg。`config.example.toml` 中的 `plugin.config_version` 为 `0.1.0`，由宿主 Runner 管理的实例配置不属于公开安装包。

## 配置

业务设置位于专属数据目录的 `settings.toml`。各模块可编辑本模块 TOML，设置总览可导入、导出完整文档；示例见 `settings.example.toml`。

页面保存后立即生效，并同步数据库和 TOML。手动编辑服务器文件后需重载插件；非法配置会保留上次有效值，并在总览显示错误。

模型路由示例见 `model-routing.example.toml`，独立提供商示例见 `provider.example.toml`。模型页可直接选择宿主模型，凭据不写入普通业务 TOML 或公开导出。

`[browse].video_understanding_mode` 可选 `frames`、`video`、`audio`、`subtitle`；选定视频模型不会自动切换理解方式。Qwen 协议和音视频调用路径见 [模型说明](MODEL_PROVIDERS.md)。

主人的数字 B 站 UID 可填入 `owner_uid`，`mention_owner=true` 时在新生成的视频评论中添加真实提及；UID 留空时不添加。已有草稿不会自动改写。

## 更新

先暂停任务、停止新发布，等待在途请求完成或确认其待核对状态。保存数据库快照和专属配置后，在宿主插件管理界面禁用插件，确认 Runner 已停止。

核对新包 SHA256，通过宿主支持的 ZIP 更新入口更新同一插件。若实例不支持覆盖导入，可在禁用状态下由管理员将包内 `bililife/` 内容更新到现有源码目录，保留原 `config.toml`、专属数据及私有文件。不要清空数据目录后重装。

启用后核对页面、账号、模型路由、任务和发布记录，再继续任务与发布。无需升级或重启 MaiBot 主程序。刚加载后的短暂 API 502 应先检查状态，不能据此重复提交评论。

不要单独升级 `bilibili-api-python==17.4.2`：评论与回复发布依赖其私有方法 `Api._prepare_request()`，上游变更可能导致发布失败。调用契约与验证要求见 [兼容性说明](COMPATIBILITY.md#评论发布的私有接口依赖)。

## 数据与凭据

运行日志页的“备份数据库”使用 SQLite online backup，快照存入 `data_dir/backups/`，权限为 `0600`。不要直接复制正在写入的 SQLite 文件。数据库快照不含账号 Cookie，仅附带不含密钥的模型路由信息。

`credential.json`、`model-secrets.json`、`settings.toml`、`model-routing.json` 和实例 `config.toml` 留在服务器受保护位置。不要将私有文件加入源码、安装包、模型输入、日志或公开报告。

数据库结构由 `migrations/` 和 `schema_version` 管理，不修改宿主数据库表。保留当前发布记录和待核对操作，结果未知的评论不能直接重发。

`security.py` 的 `redact` 基于字段名和文本正则，可能误报或漏报，只作日志、审计和导出的补充脱敏。凭据独立存储、公开响应限制字段、供应商错误仅提取错误码与分类说明，均先于这层过滤；不能先混入秘密再依赖它清除。

## 卸载

先停止自动发布并暂停任务，在宿主插件管理界面禁用，确认 Runner、组件和页面下线后移走本插件源码目录。按需保留专属数据和数据库快照用于审计与去重。若需永久清除数据，先处理结果未知的发布操作，避免重新安装后丢失平台已有发言的记录。

不需要修改其他插件、宿主源码或 systemd 服务。
