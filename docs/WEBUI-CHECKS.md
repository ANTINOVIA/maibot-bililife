# 模块卡片同步回归

前端测试使用 Node.js 内置测试运行器和 jsdom 26.1.0。测试执行完整应用脚本，在 DOM 中点击打开、保存、关闭、重开与切换开关；桥接使用隔离的内存后端，不调用真实账号、不发送消息。

在仓库根目录运行（PowerShell）：

```powershell
npm install --prefix .webui-test --no-save --package-lock=false jsdom@26.1.0
$env:NODE_PATH = (Resolve-Path .webui-test/node_modules).Path
node --test tests/webui_tiles.cjs
python scripts/build_webui.py
```

覆盖总览/设置总览保存后同步摘要和开关、不重建整个页面、关闭后保留新摘要、重开显示已保存值、直接切换模块同步摘要，以及校验失败保留草稿和原卡片。空闲浏览属于 SDK 2.20 集成分支，上游基础分支跳过该专项；集成分支会运行。

`.webui-test/` 仅为本地测试依赖目录，不应提交或部署。修改源脚本后需重新构建并提交 `webui/index.html`，运行时加载的是构建页面。
