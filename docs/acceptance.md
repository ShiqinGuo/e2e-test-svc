# 后端验证证据

本文件只记录实际完成的验证。功能实现、单元测试、真实 PostgreSQL API 集成、容器中的浏览器执行和完整 Web 操作是不同层面的证据，不能互相替代。

## 接口契约

- 已生成 `docs/openapi.json`，当前包含 30 条路径、41 个操作，以及项目、组、场景、不可变版本、环境、运行、录制、工件和认证请求/响应 schema。
- `app/openapi.py` 从 FastAPI 实际路由提取请求模型、参数和分页限制，合并经审核的成功/错误响应 schema 与 Cookie 安全定义。请求模型采用 `Request` 前缀，避免带凭据的角色输入与脱敏的角色响应名称冲突；`/api/openapi.json` 已在集成测试中核对。
- `uv run python scripts/openapi.py --check` 已通过：检查本地 `$ref`、重复 `operationId`、成功/跳转响应、非空 JSON 响应 schema 和生成文件一致性。旧 TypeScript 生成器已删除。
- `uvx --from openapi-spec-validator openapi-spec-validator docs/openapi.json` 已通过完整 OpenAPI 标准校验。标准校验不能证明运行时行为符合契约。
- 认证 schema 已同步实际 FastAPI Users 数据库会话：`e2e_session` Cookie、注册 201，以及无明文 token 的 `{user,session}` 响应。Trace 入口使用 307 跳转到同源官方 Viewer；`POST /runs/{runId}/rerun` 使用原版本与环境快照，返回 202 和新的运行 ID。

## API 集成结果

2026-09-13 本地执行 `uv run python -m pytest tests/test_api.py -q`：**12 passed in 17.82s**。每个用例使用真实 PostgreSQL 独立 schema、实际 Alembic 迁移和实际 FastAPI Users；测试退出后删除对应 schema。业务 JSON 响应逐条使用 JSON Schema 验证，包含 UTC 时间格式、空值和字段约束。浏览器执行/录制在此层使用有界 stub，实际 Playwright/noVNC 容器验证由独立运行时测试提供。

通过的 12 个用例覆盖：

1. 注册、登录、当前用户、会话、HttpOnly/SameSite Cookie，退出后旧 Cookie 撤销；公开 session ID 不能用于认证。
2. 两个账号的项目、组、场景、版本、环境、运行、重跑、事件、工件、Trace 和录制 API 隔离。
3. 同一账号的不同项目之间不能混用子资源 ID。
4. 版本不能通过 PATCH/PUT/DELETE 覆盖；普通新运行采用当前版本，`rerun` 独立保存原运行快照，后续修改代码/模块/环境不会污染历史或重跑。
5. 环境列表、详情和运行快照不泄露秘密变量、角色 Cookie/headers；PATCH 未传凭据保持原值，执行交接收到原始专属凭据。
6. 不可信 Origin 写请求被拒绝；分页、场景/组选择、角色、重试、超时和模块路径校验。
7. 失败后重试通过仍保留首次失败与第二次通过事件、`flaky` 计数；事件有序、分页可续读并脱敏。私有工件按运行和项目授权，工作目录外文件不能注册，附件带下载处置头；官方 Trace Viewer 静态页面仍经授权。
8. 取消终态幂等；“操作通过但未验证”的结果保持 `unverified`。
9. 录制停止、读取代码、保存为新 recording 来源版本；错误账号不能访问查看页面。未登录、错误账号或错误 Origin 的录制 WebSocket 握手被拒绝。
10. API 重启后会话与版本保留；中断运行显式变成 `error`，留存 `PROCESS_INTERRUPTED` 事件。
11. 网站地址、命名 API 基址、跨站 action、重复角色及秘密变量约束；没有凭据的角色仍能正常交接执行。
12. 测试组运行固化当时的成员与各版本，之后组成员和版本变更不影响已创建运行。

`uv run ruff check app/openapi.py scripts/openapi.py tests/test_api.py --output-format concise` 已通过。

## 真实 Docker 与 Python API 全链路

以下使用真实浏览器与容器，不使用 API runtime stub。

- `tests/runner.integration.test.ts`：9次真实Docker运行通过。包含UI创建订单→显示ID→API读取同一实体→双清理、模块复用、buyer/admin独立context、秘密变量引用、失败重试/flaky、跳过、零断言、零测试、真实codegen产物在A/B两环境重跑、旧环境拒绝、TCP旁路/平台端口阻断、取消和超时。Trace ZIP可解析且已知测试秘密扫描通过；证据 `data/runner-acceptance/acceptance.json`。
- `tests/recorder.test.ts`：5项AST绑定测试通过；`tests/recorder.integration.test.ts`：2项真实Docker录制集成通过，含授权/未授权HTTP+WS、真实RFB握手、路径穿越与网络隔离、停止和启动中取消。
- `runtime/recorder/evidence/adapter-result.json` 和 `novnc-desktop.png`：无网络技术夹具中，原生Inspector可见/文本断言点选Submit保持服务端提交数0；恢复普通动作录制后提交数1；值断言生成成功，浏览器noVNC显示真实远程桌面。
- `scripts/acceptance.py`：通过实际4100 API注册隔离账号、创建PG项目及版本、执行真实Chromium和API、保存真实工件。2026-09-13的 `data/api-runtime-acceptance.json` 记录3个独立运行：`c8bfab9c-6079-4d05-9fc1-b3048a061be2`（环境A）、`d24ff178-8ae9-435d-8f4f-d943e0f21812`（当前环境改B后仍按原A快照重跑）、`8a7e4642-97f1-455e-b544-87ae1a353294`（普通创建使用当前B）。全部passed/verified，每次4个私有工件；UI真实创建订单ID、API查同一ID、setup捕获seed、cleanup使用两个动态ID返回204。
- 同一脚本实际验证了已登录私有Trace官方页面、工件下载与未登录401，以及 FastAPI Cookie/Origin→noVNC HTTP→WebSocket RFB真实握手、停止后导出命名环境绑定代码。

全链路首次发现Pydantic序列化 `capture:{}` 导致204清理响应被误解析JSON，运行 `fdd6ebe4-ec2e-4336-b1f4-3894f42ee008` 保留error。修复后通过 `tests/runner.actions.test.ts` 真实容器定向回归（空capture与纯文本、204、body:null无请求体、显式false保留），再通过上述三个API完整运行，没有覆盖首次失败。

独立审查又复现录制轮询与stop竞态覆盖最终代码、stop失败仍save旧代码、运行时target丢失却保持ready。修复采用回写前重新锁定读取状态、save要求成功停止flush、target缺失明确错误；3项真实PG竞态回归已通过。它们使用有界录制stub，属于状态与并发验证，不替代noVNC真实连接证据。

最终 `uv run python -m pytest -q`：**15 passed in 18.22s**（12项API集成+3项 `tests/test_recording_consistency.py` 竞态回归），Ruff、TypeScript及OpenAPI生成一致性通过。最后变更针对录制状态/代理，未重复无关的9次运行器验收。

`runtime/recorder/evidence/python-proxy-result.json` 与 `python-proxy-desktop.png` 是修复后使用真实PostgreSQL临时schema、FastAPI临时端口、Node控制器、Docker录制容器和无头Edge完成的浏览器代理验证：Cookie/Origin授权连接noVNC成功，匿名HTTP401、另一账号HTTP404/WS拒绝、退出后已有WS在5秒重新鉴权时关闭。浏览器不接收gateway token；正常断连未再出现ASGI异常日志。该验证与上述stub并发测试分开。

完整Web工作台浏览器验收在独立前端任务执行，见前端项目的验收报告；本后端目录不把对方尚未最终交付的结果称为本目录已独立完成的UI验收。

## 独立前端任务联调回报

前端任务 `01a09661-8ef0-7953-abdd-4d8233b8274b` 在最终修复后的4100服务上报告以下真实Web操作；对应PostgreSQL运行记录仍保留，前端自己的截图与测试日志在 `D:/code/e2e-test-fronted/docs/` 归档。

| 环节 | 对应资源与结果 |
| --- | --- |
| 真实noVNC录制 | project `6bb01c56-0c83-4ace-a337-17bb0093f22b`，scenario `cd9c147c-47f8-48d0-a961-b93ab26a3e85`；可见断言点选按钮时服务端orders为空，随后动作录制实际提交订单；官方Trace完整Actions/DOM/Network可查看 |
| 第一次原始录制 | run `cfbbb393-47e9-47b8-bd76-6f4b501ef283` passed/verified，69事件，真实Trace |
| 修复后第二次录制 | recording `19ef02b5-c0f6-4263-a363-0070907cd63d`；原始v2 `02a02c75-8a8c-456d-b354-ff9d2ee76f15`；run `0e30d3b3-9403-41d2-95e1-5f869e778b60` passed/verified，50事件、6.7秒，源码保留goto/click/文本断言 |
| 在保留v2后新增v3 | v3 `c3633588-6434-4277-b666-11df50d966a8`；run `23c2a295-586f-4fea-a580-852d791f665d` passed/verified，76事件、7.3秒；UI实际显示POST201、GET `/orders/15` 200、DELETE `/orders/15` 204 |

前端另报告28项单元、2项真实浏览器回归通过，涵盖混合失败/重试/跳过/未验证、原快照重跑、取消、会话恢复和窄屏操作。这些是前端任务的独立证据，不计入本后端15项PG测试数量。最终后端小修仅限制录制checks摘要长度，完整源码与断言不变，AST组6/6通过；在上述会话全部结束后才载入，没有中断前端正在执行的录制或运行。

最终服务：`http://127.0.0.1:4100`，Uvicorn PID `49008`，源文件快照revision `9d57878d5d84`（完整SHA及Docker镜像ID见 `data/deployment-snapshot.json`），替代旧revision `8086e9af0d80`。载入前已确认活动录制/运行数0；载入后health/session/OpenAPI为200，匿名me为401。主Agent已从实际PostgreSQL直接核对上述v2/v3 run均为passed/verified。没有接入首个真实业务网站或验证远端生产部署。

收尾的旧验收输出和Python/test缓存清理被自动审批拒绝，理由仅为 `blocked by policy`，目录保留；没有更换方式绕过。真实失败/重试及最新验收工件本来就按证据保留，未删除或覆盖它们。

## 最终服务上的真实多场景整组补验

在最终服务revision `9d57878d5d84`、PID `49008` 上，前端通过Web选择并运行包含两个独立场景的同一测试组。定向前端E2E **1 passed，14.8秒**；捕获POST body只有environmentId、groupId、retries:0，没有scenarioId/versionId。以下持久事实已由本后端只读访问真实PostgreSQL独立核对：

- project `c8fc51c8-e157-4240-87be-d418849bc064`；group `18682253-94fd-48e5-8c62-375f13579554`；run `371158b1-4e45-4bb4-ac77-8bcf1cccf163`。
- 固定场景 `d7dd0f64-bf5d-4f8b-ae30-696f6d1ef21d` → 版本 `27d3734a-0cbf-4af0-a839-90982bc87500`；场景 `0a775a66-43a7-42cc-a37b-9af4c2b92086` → 版本 `dcd9d6e0-49d7-4328-b852-85fda8af953a`。运行快照与前端提交后的预期两份版本完全一致。
- 结果passed/verified，2通过、0失败、0未验证；142条持久事件，两个测试分别5条/4条实际断言；每个测试的截图和Trace都能通过实际附件路径精确关联到该run的私有artifact ID。
- 稳定关联方式：以 `(testId, attempt)` 连接test.begin和test.end，从begin读取scenarioId/versionId；将end.attachments.path的`artifacts/`前缀移除，与artifact.name精确匹配。Playwright会截短输出目录，不能根据文件名是否包含完整版本UUID判断归属。

后端复核命令：`uv run python scripts/verify_group_run.py 371158b1-4e45-4bb4-ac77-8bcf1cccf163`。结果在 `data/group-api-acceptance.json`，包含固定版本、测试身份、逐测试断言数、工件ID关联；前端完整Web请求/UI证据在 `D:/code/e2e-test-fronted/docs/group-acceptance.json`。

此前整组run `66ca7cbd-8978-4e4c-b99c-0dd1a729b0ee` 的业务执行同样passed/verified；后续验收脚本曾因目录截短导致完整UUID匹配假设失败，该运行没有被覆盖。修正的是前端验收映射，未修改后端生产逻辑。本次补验只新增只读核验脚本与文档，没有重启服务、变更核心源码版本或重复无关运行。

### 最终同名测试的场景与版本归属

前端进一步补齐了同名test在步骤详情和工件列表中的明确场景、固定版本展示。最终Web定向验收为run `21b77449-4b74-48a6-b765-254156c7b981`，project `b443527c-d172-46de-b9ba-9a0ec916720e`，group `90ecbfdb-7007-4995-b10e-18f2f1aec0f7`。前两个整组验收结论继续有效，原始运行和事件保留；`data/group-api-acceptance.json` 与前端 `docs/group-acceptance.json` 的最终索引现指本次run。

本后端使用原有只读脚本核实：两个测试标题均为`test`，但testId不同；以testId+attempt关联后的固定版本分别为：

| 场景 | 固定版本 | 实际断言 | 结果 |
| --- | --- | --- | --- |
| `0df06c34-1ae7-493e-9eb5-a49e7878e426` | `28966d9d-24bd-4704-a6b2-fef5f5bd0f8c`（v1） | 5 | passed |
| `77dbf2d1-9a77-4a9d-a521-090a39220761` | `ecfa4721-e88d-4b25-bf67-bccbb0937aa4`（v1） | 4 | passed |

汇总passed/verified，2通过、0失败/跳过/flaky/未验证；142条事件，两个场景各自的截图和Trace均按实际附件path精确关联。Trace artifact IDs为 `ba704250-87c8-4024-b0e3-7de80748880f` 和 `07e3b2fe-2833-4e6d-bbf7-ed98b1a4b760`。管理任务另已目视核对前端场景名称与固定版本展示。

本次后端仅执行 `uv run python scripts/verify_group_run.py 21b77449-4b74-48a6-b765-254156c7b981` 读取持久记录并更新此索引，没有创建新运行、修改生产实现或重启服务，部署版本仍为 `9d57878d5d84` / PID `49008`。

## 尚不能由上述检查证明

- 前端完整 UI 验收、真实业务网站适配与首次业务试点。
- 生产 TLS/反向代理配置、跨机器部署与长期运行可靠性。
- 本地 API 测试使用的 stub 不能证明容器网络隔离、codegen Inspector 嵌入、点选断言防误提交或浏览器 Trace 内容正确；这些必须引用实际运行时验证记录。
- API 工件授权测试中的 ZIP 是合成夹具，仅用于检查下载、授权和官方 Viewer 页面可达；没有将它视为真实浏览器 Trace。WebSocket 测试验证拒绝访问，成功录制连接需要运行时/Web 验收的独立证据。
