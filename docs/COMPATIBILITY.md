# 实际环境与兼容性

清单将本插件归类为 `integration`（外部平台集成），声明宿主兼容范围为 MaiBot 1.3.14–1.3.99，SDK 仍限定为 2.16.0。以下记录是实际验证环境；不代表已经逐一验证范围内所有宿主补丁版本。

核查日期：2026-10-22 至 2026-10-23。以服务器只读源码和实际调用为准。

| 项目 | 核查结果 |
| --- | --- |
| MaiBot | 1.3.14，`/opt/maibot`，无 `.git`，分支和提交无法核实 |
| SDK | maibot-plugin-sdk 2.16.0，Host/Runner v2；不是旧 BasePlugin 框架 |
| Dashboard | maibot-dashboard 1.9.8，custom 页面 schema v2，认证插件 API 桥接 |
| Python | 3.12.15，`/opt/maibot/.venv`，uv；插件在独立 Runner 中运行 |
| 启动 | systemd `maibot.service`，`/opt/maibot/.venv/bin/python /opt/maibot/bot.py` |
| 平台库 | bilibili-api-python 17.4.2，复核实际 Comment/二维码/媒体方法签名 |
| 资源 | Debian，2 CPU，约 1.6GiB 内存、5GiB swap，初检剩余约24GiB磁盘 |
| 媒体 | Debian FFmpeg，单并发异步子进程；未引入浏览器、GPU 或微服务 |
| 账号 | 新插件独立扫码，与原 bilibili_autopilot 插件隔离 |
| 模型 | 已有 utils/planner、replyer、vlm 与 embedding；voice 无模型 |
| 记忆 | 注册 `bililife_recall` 工具；真实 `maisaka.context.append`；未发现长期记忆写入 API |

已读取官方 [插件文档](https://docs.mai-mai.org/plugin/)、
[自定义页面](https://docs.mai-mai.org/plugin/webui-pages)、
[API](https://docs.mai-mai.org/plugin/api-reference) 和
[生命周期](https://docs.mai-mai.org/plugin/hooks)，再按已安装 SDK 与宿主实现核对。
不宣称其他 MaiBot/SDK/dashboard 组合已经兼容；安装包未升级宿主。

## 评论发布的私有接口依赖

`vendor/client.py` 的 `publish()` 直接调用 `bilibili_api.utils.network.Api._prepare_request()`；`publish_reply()` 也复用这条发布路径。当前平台库在 `_manifest.json`、`pyproject.toml` 和依赖锁定文件中固定为 `bilibili-api-python==17.4.2`。

该私有方法用于准备签名、参数和凭据。插件随后读取返回对象中的 `url`、`params`、`headers`、`data`、`cookies`，使用自身 HTTP 客户端发送单次 POST，而不调用 `Api.request` 的 WBI 重试循环；签名准备完成后还会再次检查是否允许发送。

`_prepare_request()` 不属于稳定公共 API。上游改变方法签名、异步调用方式、签名逻辑或返回字段，都可能使评论和回复发布失败。版本锁定只约束当前依赖组合，不保证其他版本可用；不要单独解除锁定或升级平台库。

升级平台库前，应先停止自动发布，在隔离环境复核上述调用契约，并检查签名和 Cookie/CSRF 传递、根评论与父评论参数、主人提及映射、发送前停止检查及单次 POST 行为。离线回归不能代替真实平台验收；公开发布验证仍需明确授权的测试对象与正文。恢复运行前先核对已有发送状态，结果未知的操作不得直接重发。

## 宿主源码基线

宿主关键源码 SHA256（部署前后核对一致）：

```text
bot.py
253a152b79e2fb82159e20e0a1f3919b2ae222b333b3aaa8472c3f89c493b436
pyproject.toml
1d98586338cace4abaffa272e83091374e4e6b63c6775cb432c518a58305e362
src/plugin_runtime/webui_schema.py
4a9de0fb7c2303684d0717b061b79ae44f684bc7dfd082f1fee6fcba4599736d
```

现有插件保持原状，只复用已有 MIT 平台客户端代码并保留署名，详见 `THIRD_PARTY.md`。
没有调用未知记忆方法、直接改宿主数据库或重启主服务。
