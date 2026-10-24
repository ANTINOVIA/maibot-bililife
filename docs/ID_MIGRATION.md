# 插件 ID 迁移与回滚

当前唯一运行标识是 **`ANTINOVA.maibot-bililife`**，区分大小写。版本保持 `0.2.6`。
GitHub 仓库所有者仍是 `ANTINOVIA`；这两个名称相互独立，不应自动改写仓库地址或许可证署名。

旧安装的 ID 为 `local.maibot-bililife`。直接将新包当作另一个插件导入，会产生独立数据目录，
因此已有安装应使用迁移脚本，而非同时启用两份插件。历史备份、旧 Git 标签及本说明中的
旧标识仅用于追溯和回滚；运行清单、维护脚本和页面地址均使用新标识。

## 新路径

| 用途 | 路径 |
| --- | --- |
| 源码 | `/opt/maibot/plugins/ANTINOVA_maibot-bililife` |
| 数据、配置、独立扫码凭据及模型密钥 | `/opt/maibot/data/plugins/ANTINOVA.maibot-bililife` |
| 临时媒体目录 | `/opt/maibot/temp/plugins/ANTINOVA.maibot-bililife` |
| WebUI 总览 | `/extensions/ANTINOVA.maibot-bililife/overview` |
| 后端前缀 | `/api/webui/plugins/runtime/webui/ANTINOVA.maibot-bililife/overview/` |

## 服务器迁移

脚本针对已核实的单实例 MaiBot 标准布局运行，默认根目录 `/opt/maibot`、宿主 WebUI
`http://127.0.0.1:8001`。其他实例通过 `--root`、`--url` 显式指定。认证只在服务器读取
宿主已有登录文件，不打印或传输 Cookie、Token、账号凭据或模型密钥。

上传新安装包与 `scripts/migrate_plugin_id.py` 后，先检查：

```sh
/opt/maibot/.venv/bin/python /tmp/migrate_plugin_id.py \
  --archive /tmp/ANTINOVA.maibot-bililife-0.2.6.zip \
  --sha256 <对应包的SHA256> --check
```

检查通过后去掉 `--check` 执行迁移。脚本会：

1. 校验新清单、ZIP 路径和校验值；新 ID 目录已存在或含符号链接时拒绝覆盖、合并。
2. 通过宿主正常禁用旧插件，释放执行锁；在服务器的私有备份目录保存旧源码、数据与一致的 SQLite 快照。
3. 临时暂停调度和新发布，将三个目录移至新命名空间，更新源码，保留宿主 `config.toml`。
4. 加载新 ID，检查数据数量及凭据/配置文件哈希；确认安装列表只有新 ID，再恢复原暂停、发布开关。

不修改宿主数据库表，不重新扫码，不重置额度，不改写历史记忆或评论正文。失败时回退旧目录和源码；
发送结果未知的操作仍保留为未知，不通过身份迁移重发。脚本输出备份路径与非秘密的验证结果。

## ID 回滚

仅在需要恢复旧插件身份时使用以下命令；普通新 ID 内的源码回滚继续使用 `restore_server.py`。
不要将旧 ID 的源码备份直接覆盖到新目录。

```sh
/opt/maibot/.venv/bin/python /tmp/migrate_plugin_id.py \
  --rollback /opt/maibot-bililife-backups/id-migration-<时间戳>
```

回滚会禁用新实例并移动**当前**数据回旧目录，恢复备份源码，不用旧数据库快照覆盖当前 outbox。
因此迁移之后新增的经历和已确认的发布状态都会保留。回滚脚本只接受受控备份目录中的迁移检查点。

迁移完成后请更新书签和外部调用方的插件 ID。旧页面和接口不会继续作为运行别名提供服务。
