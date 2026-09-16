# Playwright 运行引擎

主 API 使用 Python/FastAPI；本目录只负责 Playwright 的 Node 工具层。运行入口为 `src/runtime/runner.ts` 的 `DockerRunner`，不在 API 进程执行用户代码，也不存在本机执行 fallback。

## 构建与验证

```powershell
docker build -f runtime/runner/Dockerfile -t e2e-runner:1.63.0 .
$env:E2E_RUN_DOCKER_TESTS='1'
node --import tsx --test tests/runner.integration.test.ts
```

镜像锁定官方 Playwright `v1.63.0-noble` 和 `@playwright/test@1.63.0`；镜像 Node 实测为 24.20.0。宿主只需要 Docker Linux、Node 24 和项目中的 tsx。验收夹具位于 `runtime/runner/fixture-server.mjs`，成功的完整测试会写入 `data/runner-acceptance/acceptance.json`，它只证明平台技术链路，不代表真实业务网站验收。

## Node/Python bridge 接口

`new DockerRunner({image?:string})`；`available()` 返回 `{available,reason?}`；`start(input,handlers)` 安排后台执行后返回；`cancel(runId)` 发出终止信号并留出清理时间；`close()` 取消并等待所有活动运行。

`RunInput` 字段：`id,projectId,workDir,versions:[{id,scenarioId,code,checks,modules}],environment:{websites,apiBases,variables,secretVariables,roles,setup,cleanup,allowedOrigins},role?,timeoutMs,retries,platformOrigins?`。输入在 API 层已经固化；Node 不自行查询平台数据库。API 必须提供专属于该运行的新目录。

`handlers.event({type,timestamp,data})` 持续发送事件。API 给事件增加自己的单调 `seq` 并持久化。`handlers.complete({status,verification,summary,error?,artifacts})` 只发送一次最终结果。每个 artifact 为 `{name,path,contentType,kind}`，`path` 是内部绝对路径；API 不向客户端透出，并再次核对真实路径、项目归属与会话。

## 实际事件结构

所有时间为 ISO UTC；`attempt` 从 0 起算，0 是首次执行，1 是第一次重试。`testId` 是 Playwright 本次固化运行中的测试 ID。

| type | data 字段 |
| --- | --- |
| `run.begin` | `runId,versionIds,allowedOrigins` |
| `suite.begin` | `total,workers` |
| `test.begin` | `testId,versionId,scenarioId,title,file,attempt,status:'running'` |
| `step.begin` | `testId,attempt,stepId,parentStepId?,title,category,location?` |
| `step.end` | `testId,attempt,stepId,parentStepId?,title,category,durationMs,status:'passed'|'failed',error?,expected?,actual?` |
| `assertion` | `testId,attempt,matcher,negated,expected:args[],actual,status,error?` |
| `network` | `testId?,attempt?,phase:'request'|'response'|'failed',url,method?,resourceType?,status?,error?,source?:'api'` |
| `console` | `testId,attempt,level,text` |
| `page.error` | `testId,attempt,message` |
| `stdout`,`stderr` | `testId?,attempt?,text` |
| `setup.begin`,`cleanup.begin` | `name,apiBase,method,path` |
| `setup.end`,`cleanup.end` | `name,status,targetOrigin?,expected?,actual?,captured?:string[],durationMs,error?` |
| `test.end` | `testId,title,attempt,status,expectedStatus,durationMs,assertions,errors:[{message,stack}],attachments:[{name,contentType,path?}]` |
| `suite.end`,`run.complete` | `status,verification,summary,error?` |
| `error` | `message,stack?` |

常规 `@playwright/test` 导入在临时副本中指向平台 fixtures，原始固化版本不改变。实际断言计数来自官方 reporter 的 `category:'expect'`，绝不会根据 `checks` 声明判绿。`assertion` 事件补充 matcher 参数与执行后的观测值，文本、值、可见性、启用状态、URL、标题、数量、属性和普通 JSON 值均有明确支持；无法安全读取的自定义对象返回 `{unavailable:true}`，断言原始失败内容仍保留在 reporter/trace 中。它不是给每种自定义 matcher 编造 actual 的通用反射器。

## 结果语义

- `summary.total` 是实际收集的测试数；最后 attempt 通过计入 `passed`，最后失败计入 `failed`，跳过计入 `skipped`。某次失败后重试通过还计入 `flaky`，之前 attempt 的失败事件与 trace 保留。预期失败 (`test.fail`) 的实际失败也仍计入 `failed`。
- 已执行但最后 attempt 的断言数为零的测试计入 `unverified`，仅操作、仅声明检查项、在第一条断言之前失败均属于此类。所有测试跳过或未验证时 `verification:'unverified'`，部分跳过/未验证时为 `partial`，其余为 `verified`；verified 表示执行了验证，不表示测试通过。
- 没有收集测试是 `status:'error'`，不能显示成功。准备或清理失败是 `error`；清理错误保留在错误字段。运行取消为 `cancelled`，总时限到达为 `timed_out`。
- 取消/超时会给 Playwright 中断信号；清理最多享有 Docker stop 的 20 秒宽限。强杀、Docker 故障或主机断电时无法承诺清理一定完成，API 启动恢复应保留中断错误并清理标签 `e2e.platform=true` 的遗留容器和网络。

## 场景与环境绑定

```ts
import {test, expect} from '@playwright/test';
test('order is visible and queryable', async ({page, platform}) => {
  await page.goto(new URL('/orders', process.env.E2E_WEBSITE_main!).href);
  await expect(page.getByText(platform.get('orderId'))).toBeVisible();
  const response = await platform.api('main', '/orders/' + platform.get('orderId'));
  expect(response.status()).toBe(200);
  platform.set('followupId', (await response.json()).id);
});
```

每个命名网站提供 `E2E_WEBSITE_<name>`，每个 API 基址提供 `E2E_API_<name>`，预配置 variables/secretVariables 提供 `E2E_VAR_<name>`（用于录制代码引用命名值，避免把秘密写成代码字面量）。`platform.websites/apiBases/variables/get/set` 访问固化环境及本次运行变量。setup 的 `capture:{orderId:'id'}` 从 JSON 响应读取动态值，场景内 `platform.set` 的值会给后续场景和 cleanup 使用（单 worker，按固化场景顺序）。动态捕获请通过 platform.get 读取；变量文件不跨运行共享。

当前供前后端联调的常驻技术夹具容器为 `e2e-demo-fixture`，宿主页面 `http://localhost:18080/`、`http://localhost:18081/`，worker 环境入口/API 基址使用 `http://host.docker.internal:18080`、`:18081`。页面的 Order 输入与 Submit order 按钮真实 POST `/orders`，`#order-id` 显示响应 ID；GET `/orders/:id` 校验同一记录，DELETE `/orders/:id` 清理，GET `/state` 查看本夹具记录。它仅保存内存数据，重启后清空，且不属于第一个业务网站接入。

setup/cleanup `path` 必须相对已命名 API 基址，`{{name}}` 可在 path/headers/body 插值；不接受绝对 URL、协议相对 URL 或反斜线逃逸。响应状态必须等于 `expectedStatus`，省略时必须是 2xx；自动重定向关闭，防止准备和校验意外切换目标。cleanup 总会尝试，并在一个动作失败后继续后面的清理动作。

Python 序列化的 `body:null` 表示不发送请求体，`headers:{}` 表示不增加动作级 headers；`false`、`0`、空字符串仍按显式请求体发送。`capture:{}` 不解析响应 JSON，因此 204 或纯文本响应都可按状态码成功；只有非空 capture 才要求响应是 JSON。该兼容回归为 `tests/runner.actions.test.ts`，使用 `E2E_RUN_DOCKER_TESTS=1` 单独运行即可。

所选 role 的 storageState 和 headers 只用于本次默认浏览器/API context。`platform.roleContext(name)` 可以创建环境内另一个角色的独立 browser context，并自动在测试结束后关闭。每个测试仍使用 Playwright 默认独立 context；无浏览器共享 profile。

## 隔离与工件

每个运行有一个 `--internal` 且 `gateway_mode_ipv4=isolated` 的 Docker network，测试容器只接此网络、无 gateway、外部 DNS 指向自己的空 loopback；无 Docker socket、API 数据库、平台 Cookie 或宿主凭据挂载。它以 UID 1000 运行，根文件系统只读、drop ALL capabilities、no-new-privileges，限制内存、CPU、进程数与共享内存。仅本次 execution 目录可写。

双网卡 egress sidecar 只接收 HTTP proxy/CONNECT，按本次环境的 websites/apiBases/allowedOrigins 核对目的地；阻断平台 origins、平台显式端口、Docker 控制端口、loopback 和 link-local metadata。代理 DNS 在连接时解析并检查实际地址，测试本身不直接解析外网。HTTPS CONNECT 只能核对目标端点，TLS 内容仍端到端加密。自托管环境允许显式配置的内网测试地址。平台与测试应用不应共用同一物理 IP+443 端点：为防止 Host/SNI 变化命中平台，该端点会整体拒绝。

工件包括每次 attempt 的 trace、截图、页面与网络快照、失败上下文、网络 JSON 和脱敏事件日志，均由 API 私有授权接口提供。重试不覆盖前一次工件。官方 Trace Viewer 由主 API 托管。已知 secretVariables、role headers、Cookie/localStorage 值以及 Authorization/Cookie 字段在事件、文本附件和 trace ZIP 文本条目中替换；凭据 input/config/变量文件在容器停止后删除。工件脱敏由测试容器退出后的独立、无网络、受资源限制的可信容器执行，取消/强杀同样经过此步骤。脱敏失败时只交付已脱敏事件日志，原始工件不登记给 API。截图中的页面内容无法通用识别秘密，密码输入应由被测页面使用 password 类型；不能承诺自动擦除任意页面图片里的凭据。

这是 Docker 隔离的测试执行服务，不是针对有意利用内核漏洞的多租户安全沙箱。导入代码可以改变自身测试逻辑；平台保留原始不可变代码和完整结果，不把自行篡改执行器的恶意代码当可信合格证明。API 仍须执行账号/项目隔离，不能因容器存在就省略资源鉴权。

官方依据：[Docker](https://playwright.dev/docs/docker)、[Reporters](https://playwright.dev/docs/test-reporters)、[Fixtures](https://playwright.dev/docs/test-fixtures)。
