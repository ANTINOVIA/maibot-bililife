# 安装、部署与回滚

以下路径来自已验证部署环境。部署到其他服务器前，先从实际配置确认地址、SSH 用户、
端口和安装位置，不要猜测账户。私钥仅由 SSH 客户端使用，不得复制进插件或安装包。

宿主 `/opt/maibot` 使用 Python 3.12.15、uv 管理环境，由 `maibot.service` 启动。
插件安装路径 `/opt/maibot/plugins/ANTINOVA_maibot-bililife`。
持久化路径 `/opt/maibot/data/plugins/ANTINOVA.maibot-bililife`。
临时媒体目录由 `ctx.paths.runtime_dir` 注入，默认 `/opt/maibot/temp/plugins/ANTINOVA.maibot-bililife`。
没有新建公网服务；页面通过宿主 WebUI v2 桥接调用后端。

## 首次安装

先核实本包清单声明的宿主兼容范围（MaiBot 1.3.14–1.3.99）、SDK 精确版本（2.16.0）、dashboard 1.9.8 页面协议以及已有依赖。实际验证的宿主版本为 1.3.14，其他补丁版本尚未逐一验证。
在 WebUI 插件管理导入 ZIP，禁用再启用插件以触发宿主配置热应用；不需要升级主程序。
`config.example.toml` 包含 SDK 2.16 必需的 `plugin.config_version`。业务默认设置可在专属页面保存。
FFmpeg 是可选系统依赖；本服务器已从 Debian 仓库安装，不依赖浏览器或 GPU。
若 ASR 角色没有模型，不应打开 ASR 开关。

## 本次访问方式

服务器 WebUI 只监听本机 8001。当前工作机 SSH 隧道入口：
`http://127.0.0.1:18001/extensions/ANTINOVA.maibot-bililife/overview`。
用现有 MaiBot WebUI 登录方式进入，B 站登录在插件设置页单独扫码。
不要把 WebUI Token 放进 URL 或文档。

重新建立隧道：

```powershell
ssh -i <私钥路径> -p <SSH端口> -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -N -L 127.0.0.1:18001:127.0.0.1:8001 <SSH用户名>@<服务器地址>
```

## 升级

首次切换到新插件 ID 时，先按 [ID 迁移与回滚](ID_MIGRATION.md) 操作；下面的升级和源码恢复脚本用于新 ID 内的后续维护。

不要单独升级 `bilibili-api-python==17.4.2`：评论与回复发布依赖其私有方法 `Api._prepare_request()`，上游签名或返回结构变化可能使发布失败。升级平台库前先停止自动发布，并按 [私有接口依赖说明](COMPATIBILITY.md#评论发布的私有接口依赖) 复核调用契约和发布行为；不得以升级或回滚为由重新发送结果未知的操作。

本地先运行测试、构建和打包，核对 SHA256，将 ZIP 上传服务器 `/tmp/`。
`scripts/deploy_server.py ZIP SHA256` 只操作本插件目录；升级前暂停、备份源码与在线 SQLite
快照，利用宿主配置接口卸载再加载，不重启 MaiBot 主进程。示例：

```sh
/opt/maibot/.venv/bin/python /tmp/deploy_server.py /tmp/ANTINOVA.maibot-bililife-0.2.6.zip <本地生成的SHA256>
```

脚本在 `/opt/maibot-bililife-backups/时间戳/` 保留私有备份与宿主源码哈希基线。
初次安装前无插件数据库时只记录基线。热更新文件可能触发源码监听器，刚加载后的短暂
API 502 应先查询状态，禁止据此重复提交发布操作。当前插件设置与扫码凭据不会被安装包覆盖。
清单已指向本项目独立仓库；建议使用 Releases 的版本化安装包升级。
宿主“从仓库更新”的端到端流程尚未验收，不能将仓库源码 ZIP 当作已验证安装包。

## 数据备份与恢复

WebUI 运行日志页“备份数据库”使用 SQLite online backup，能一致地保存 WAL 中的数据；
不使用直接复制正在写入的 `.sqlite3` 文件。快照存入 `data_dir/backups/`，权限 0600。
数据库备份不包含账号 Cookie。凭据文件应在服务器受保护备份系统中单独保存；
回滚本插件时保留现有凭据文件即可，不应通过聊天或安装包传送它。
部署脚本会在服务器私有备份目录额外保存 `credential.json`、`model-routing.json` 和
`model-secrets.json`（存在时）；不要把这些私有备份上传到源码仓库或发到本地验收报告。
WebUI 数据库备份只附带不含密钥的模型路由快照。

迁移由 `migrations/001_initial.sql`、`002_module_controls.sql`、`003_autonomous_life.sql`、`004_comment_reading.sql`、`005_budget_reservations.sql` 和 `schema_version` 管理。升级前留快照；
迁移5保存按日、按视频分 P 的浏览额度占用。旧记录已有证据时补记首次浏览当日的占用标识，保留原额度账目，不自动修改历史消耗；重试沿用占用，实际模型调用和流量继续据实记录。
本版本不修改宿主数据表。未来破坏性迁移必须提供向前兼容方案或停机恢复说明。

源码回滚默认保留当前数据库，防止丢失远端已成功或未知的发布记录。
恢复脚本与同目录的 `restore_settings.py` 应一起上传或直接在插件 `scripts/` 目录运行：

```sh
/opt/maibot/.venv/bin/python scripts/restore_server.py /opt/maibot-bililife-backups/<时间戳> --check
/opt/maibot/.venv/bin/python scripts/restore_server.py /opt/maibot-bililife-backups/<时间戳>
```

加 `--data` 才恢复 SQLite。恢复器先做完整性检查，并拒绝任何会丢弃或回退现有发布操作的
旧快照。拒绝时应保留当前 outbox、仅回滚源码；不能把备份中不存在某条评论视为它没发出。
恢复后调度保持暂停，先核对账号、未知发布及任务，再从页面继续。
从0.2.5回滚0.2.0时会适配旧配置字段；无法等价表达细分发布权限时退回不自动发布。
当前 `settings.toml`、账号与模型密钥默认保留。再次升级0.2.5时可从TOML恢复细分设置。

## TOML 配置

主要业务配置文件为 `data_dir/settings.toml`（0600）。模块页面可直接编辑对应节，
设置总览可导入/导出整个文档，模板见 `settings.example.toml`。
页面保存会立即更新有效配置、SQLite快照和TOML；手动编辑服务器文件后重载插件生效。
非法文件不会清空配置，保留上次有效值并在总览显示错误。
数据库恢复默认保留当前TOML，因此当前权限设置优先；如确需恢复旧配置，单独核对并使用备份中的TOML。
发布outbox不能回退，即便配置恢复也不能盲目重新提交未知操作。

模型选择可在模型页直接下拉，或用 `model-routing.example.toml` 的格式输入；
独立提供商可参考 `provider.example.toml`。提供商密钥不写入普通业务TOML及导出。

理解方式使用 `[browse].video_understanding_mode`，可选 `frames`、`video`、`audio`、`subtitle`。
旧的三个媒体开关在启动时迁移，保留原有效处理路径；升级不会因配置了视频模型就自动改用视频。
Qwen 兼容与 DashScope 原生配置见 `provider.qwen.example.toml` 和 `docs/MODEL_PROVIDERS.md`。
若回滚到不支持这些协议的旧源码，应先暂停任务、保留当前凭据，再恢复部署备份中的模型路由；
不要把新增 Qwen 协议名称留给旧版继续调用。业务媒体单选在回滚时转换为旧开关。

## 0.2.6 自主生活设置

升级会移除旧 `allowed_videos` 特殊放行和固定 `concurrency` 字段，保留现有主题、账号及互动权限。
主计划按兴趣自主发现内容，无需填视频。`adaptive_schedule_enabled` 允许经历触发的自主追加，
`max_dynamic_tasks` 限制当日追加任务数。调度保持单机顺序执行。

评论与回复的自动发布开关仍分别保存，不因升级自动开启。开启后无需逐条确认；
既有人工草稿和结果未知的发布不会被自动队列重新提交。
`owner_uid` 填主人的数字 B 站 UID，`mention_owner=true` 在新生成的视频评论中添加真实提及。
留空时不添加；已有含主人提及的草稿在主人配置变化后需重新生成。

## 卸载

先停止自动发布并暂停任务，在宿主禁用插件，确认组件与页面下线。
保留 `data_dir` 和备份以便恢复、审计和去重；移走本插件源码目录即可。
不删除其他插件、不修改宿主配置、数据表或 systemd 服务。若将来永久清除经历，
先处理未知发布；避免重新安装后丢失平台已有发言的去重记录。

## 同版本修复：模型诊断与只读评论

应用版本仍为0.2.6，数据库迁移到版本4。模型诊断独立于生活资源预算；
只读评论任务为 `read_comments`，使用单独阅读次数上限，仍受日常模型预算与活跃时段约束。
评论经历保存在 `comment_reads`，不会被标记为视频观看，也不会自动生成视频评论草稿。
