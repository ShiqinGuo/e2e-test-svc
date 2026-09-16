# Web 录制运行时

录制器使用 Playwright 1.63.0 原生 codegen Inspector、Xvfb、fluxbox、x11vnc 和 noVNC 1.6.0。用户在 Web 工作台嵌入的桌面里操作真实 Chromium；保存的是可由 Playwright Test 重跑的 TypeScript。noVNC 只负责传输桌面画面和输入，不提供独立业务录制逻辑。

## 构建与技术验证

在项目根目录执行：

```powershell
docker build -f runtime/recorder/Dockerfile -t e2e-recorder:1.63.0 .
node --import tsx --test tests/recorder.test.ts
$env:RUN_DOCKER_TESTS='1'
node --import tsx --test tests/recorder.integration.test.ts
```

原生 Inspector 小验证在无网络容器内创建独立表单夹具，并在 Chromium 中连接 noVNC：

```powershell
New-Item -ItemType Directory -Force runtime/recorder/evidence | Out-Null
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 --shm-size 512m --mount "type=bind,source=$((Get-Location).Path)\runtime\recorder\evidence,target=/work" --entrypoint sh e2e-recorder:1.63.0 /opt/recorder/test-entrypoint.sh
```

已执行的验证产物在 `runtime/recorder/evidence/`：`adapter-result.json`、`recording.spec.ts` 和 `novnc-desktop.png`。验证通过可见与文本两种断言点选提交按钮时，服务端提交计数仍为 0；点选值断言产生 `toHaveValue`；回到动作模式点击提交后计数为 1。随后真实浏览器加载 noVNC 页面、完成 WebSocket/RFB 连接，并保存两个原生窗口的画面。缺少或错误 gateway token 的请求返回 401。这不替代平台登录、前后端工作台或真实业务网站验收。

宿主集成测试也已通过：DockerRecorder 创建全部容器后，可经固定目标 ingress 中继获取页面与 RFB 握手；未授权 WebSocket 返回 401；路径穿越返回 404；录制容器直接访问公网失败。停止后代码已绑定环境、凭据文件已删除，重复停止幂等；在启动期间停止同样释放全部录制容器。AST 的 6 项测试覆盖命名网站、检查项提取、未知导航拒绝、真实录制产物一致性、已知秘密的变量绑定和长断言摘要长度。

### PostgreSQL 状态一致性回归

`uv run python -m pytest tests/test_recording_consistency.py -q` 已通过 3 项回归。测试使用独立 PostgreSQL schema 和受控 runtime stub，确认旧 GET 轮询不能覆盖 stop 刷新的最终代码与状态、runtime target 消失不能继续报告 ready、stop/flush 失败不能用旧轮询代码创建成功版本。它验证数据库并发与失败语义，不声称运行真实浏览器。

### Python Cookie/Origin 代理与真实浏览器

`tests/recorder_proxy_probe.py` 为每次检查创建临时 PostgreSQL schema、独立 API 端口和技术页面，通过真实 Python API → Node bridge → DockerRecorder → noVNC，在宿主 headless Microsoft Edge 中验收。结果在 `runtime/recorder/evidence/python-proxy-result.json`，画面在 `python-proxy-desktop.png`。

已验证 Cookie 认证后 noVNC 连接成功且能看到 Inspector 和目标页面；浏览器无需 gateway token；匿名 HTTP 返回 401，其他账号 HTTP 返回 404、WebSocket 握手拒绝；退出账号后已连接的 noVNC WebSocket 在重新授权检查时关闭。检查完成会停止临时 API、录制容器并清理临时 schema/数据目录。它不改动本地常驻 API 或前端的活动会话。

```powershell
$env:PYTHONPATH=(Get-Location).Path
uv run python tests/recorder_proxy_probe.py
```

这个探针目前使用本机已安装的 Microsoft Edge（`channel: 'msedge'`），因此与 Linux 内部 Inspector 夹具分开执行。上述结果证明录制查看链路；业务工作台完整的录制、保存、选择环境与重跑由主任务另行验收。

## 后端集成约定

`src/runtime/recorder.ts` 导出 `DockerRecorder`，由主 API 的 Node 工具桥持有单实例。`start` 输入 `{id,workDir,url,environment,role?,expiresAt}`；url 必须精确匹配该环境的命名网站。角色不存在时拒绝启动。`ready` 和 `error(message)` 回调通知主 API 持久化状态；`read`、`stop` 返回 `{code,checks}`。停止会刷新 codegen 输出，释放容器/网络并移除工作目录中的凭据输入。每次录制都有独立目录和容器，结束的代码由主 API 另存不可变版本。

`target(id)` 是仅供服务端使用的 `{host:'127.0.0.1',port,token}`。上游 HTTP 和 WebSocket 必须注入 `x-recorder-token: <token>`。不要向前端返回这组数据或转发平台 Cookie、Authorization。前端拿到的是已认证同源 `viewerUrl`，应以 `/index.html` 结尾，或将目录 URL 规范化为带末尾 `/`。页面中的 `./viewer.js` 和 `./websockify` 使用相同目录前缀。

主 API 只允许浏览器代理 `index.html`、`viewer.js`、`websockify`，并在每个 HTTP 请求及 WS 握手时核对当前会话、项目 owner 和录制归属。`/health`、`POST /stop` 是 Node 管理接口，不应反代给浏览器。WebSocket 断开及过期后不可重新连接。代理连接丢失时 noVNC 明确显示断开；Node 定期检查容器健康并持久化错误回调。

## 容器隔离与环境绑定

真实浏览器容器只有 internal isolated bridge 网络、没有默认出口；关闭外部 DNS，使用固定 IP 的 egress HTTP 代理。代理复用 `runtime/runner/egress.mjs`，只允许该环境网站/API及显式 allowedOrigins，拒绝平台来源、平台/控制端口、loopback 和 link-local 地址。浏览器额外拦截未配置 origin 的页面请求，并关闭 Service Worker。

Docker 的 isolated bridge 不支持直接 publish，所以通过独立固定目的 TCP ingress 容器连接该录制容器的 6080 端口，再只向宿主 127.0.0.1 发布随机端口。这个中继不接受任意目标地址；HTTP/WS 仍在录制 gateway 验证随机 256-bit token。x11vnc 只监听录制容器自身 127.0.0.1:5900，没有公开 VNC 端口。容器运行非 root、只读根文件系统、drop all capabilities、no-new-privileges，并限制 CPU、内存和进程数。主 API 数据库、会话密钥、Docker socket 和其他项目目录不会挂入浏览器容器。

Playwright codegen 生成的绝对 `goto` 字面量在导出时通过 AST 转成 `process.env['E2E_WEBSITE_<name>']!` 或基于它的相对 URL。初始入口精确匹配时直接使用新环境的完整入口，后续同 origin 导航保留 path/query/hash。未知网站导航拒绝导出，防止录完后切换环境仍访问原地址。其他断言字符串、注释不被替换。Runner 为每次执行提供固化环境变量；多网站同 origin 时，优先精确入口匹配，否则取环境中第一个同 origin 的命名入口。

代码中已配置 `secretVariables` 的完整字面量参数会转成 `process.env['E2E_VAR_<name>']!`，检查描述显示变量名，避免用脱敏占位值执行错误操作。停止时原始代码文件被环境绑定后的代码替换，内存中不再保留角色会话与输入秘密。没有预先配置的秘密无法按变量名称自动绑定；录制登录前应在环境中配置秘密变量或选择已有角色会话。

检查项元数据是可读摘要：脱敏后的 `title` 最多保留前 200 个 Unicode 字符，`expected` 最多保留前 4000 个，与 API 字段限制一致；截断不会拆开 emoji 等 Unicode 字符。完整 `code` 保留原始定位器和断言，仍是实际重跑与追查的权威来源，摘要长度限制不修改执行内容。

## 版本适配边界

生产代码唯一使用的 Playwright 私有 API 是 `BrowserContext._enableRecorder`，集中在 `runtime/recorder/adapter.mjs`，参数跟随锁定版本的官方 codegen 实现。启动时检查包版本及方法存在性，升级必须先通过 Inspector 小验证。测试为了操控 Inspector 使用上游自己的 `recorderAppForTest`/CDP 测试接口，生产路径不启用这些测试接口。

Playwright 镜像锁定 1.63.0 及 SHA256，noVNC 使用官方 v1.6.0 原始源码归档并核对 SHA256；未修改上游代码。npm 发布的 noVNC 1.6.0 包在当前构建器中存在 CommonJS 与顶层 await 冲突，因此使用相同版本的官方原始 ES module 源码打包。第三方源码及许可证保留在镜像 `/opt/recorder/novnc/`。

官方依据：[Playwright codegen](https://playwright.dev/docs/codegen)、[codegen 实现](https://github.com/microsoft/playwright/tree/v1.63.0/packages/playwright-core/src/cli)、[Inspector 上游测试](https://github.com/microsoft/playwright/blob/v1.63.0/tests/library/inspector/inspectorTest.ts)、[noVNC 1.6.0](https://github.com/novnc/noVNC/releases/tag/v1.6.0)、[Playwright Docker](https://playwright.dev/docs/docker)。
