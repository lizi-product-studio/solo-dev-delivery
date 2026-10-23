已按 TASK.md 与 APP_CONTRACT.md 完成当前演示的整理。

- 将宣传区、实现适配器说明和常驻教程改为队列与详情双栏；帮助通过右上角入口按需打开。
- 保留全部五条原始工单及长文本、多语言、状态、金额。搜索支持工单号、客户名、商品名，提供清空和无结果恢复。
- 每条工单分别暂存未保存草稿，搜索和切换不丢输入；队列和页头标明未保存项。刷新或关闭前仍须点击保存。
- 兼容 `returns-desk-demo:v1` 格式。保存前读取最新数据，只更新当前工单；真实写入后才显示成功。读取异常、写入异常和损坏数据均保留输入并说明失败。
- 保留 `?failSave=1` 故障演示、明确的退出确认、本机保存边界及离页保护。页面不连接真实业务系统。

直接打开 [index.html](index.html)，或在项目根目录运行：

```powershell
python -m http.server 8765 --bind 127.0.0.1
```

然后访问 `http://127.0.0.1:8765/`。故障演示为同一地址加 `?failSave=1`。

已完成 Chrome 155 本地浏览器验证：50 项功能与布局检查，以及最终改动后的 12 项定向检查。没有重跑未受最终改动影响的完整功能集。另通过 JavaScript 语法、差异空白检查，并与原文件对比确认五条工单数据完全保留。

检查明细保存在 [verification/results.json](verification/results.json)。实际截图包括 [桌面工作台](verification/workspace.png)、[1366×900 长内容](verification/desktop-1366.png)、[窄屏详情](verification/mobile-detail.png)、[帮助](verification/help.png) 和 [保存失败](verification/save-failure.png)。

自动检查不需要第三方依赖，使用 Node 24 自带 WebSocket 连接 Chrome DevTools。复现时先运行上述本地服务，再在项目根目录的另一个 PowerShell 中启动独立测试浏览器：

```powershell
$profilePath = Join-Path (Get-Location).Path '.browser-check'
$chromeArgs = @(
  '--headless=new', '--disable-gpu', '--no-first-run',
  '--no-default-browser-check', '--disable-background-networking',
  '--disable-component-update', '--disable-sync', '--disable-extensions',
  '--no-proxy-server', '--remote-debugging-address=127.0.0.1',
  '--remote-debugging-port=9222',
  ('--user-data-dir="' + $profilePath + '"'), 'about:blank'
)
$testBrowser = Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList $chromeArgs -WindowStyle Hidden -PassThru
node verify.mjs
node verify-layout.mjs
Stop-Process -Id $testBrowser.Id
```

测试会修改独立测试配置中的演示备注，不应连接日常使用的浏览器配置。交付时已清理本次临时浏览器配置。

验证范围为本机 Chrome、真实键盘操作、桌面截图及浏览器模拟的窄视口；未在其他浏览器、实际移动设备或屏幕阅读器中验收。离页检查验证了未保存状态与拦截逻辑，故障退出确认实际打开并测试了取消与放弃；普通关闭页面的原生提示措辞由浏览器决定。
