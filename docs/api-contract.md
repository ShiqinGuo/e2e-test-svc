# Web 业务流程测试平台 API v1

状态：后端 FastAPI + PostgreSQL 已启动，前后端联调中；地址 `http://localhost:4100`。开发前端请同源代理 `/api` 到后端（WebSocket 也代理），浏览器请求用 `credentials: 'include'`。生产同域 HTTPS。时间为 ISO 8601 UTC，ID 为 UUID 字符串。

## 通用约定

- 认证复用 FastAPI Users 的 Argon2 密码与 PostgreSQL 数据库会话，兼容路由使用 `/api/auth`。Cookie `e2e_session` 为 HttpOnly、SameSite=Lax；生产启用 Secure。前端无需保存 bearer token。
- 注册 `POST /api/auth/sign-up/email`，body `{name,email,password}` 返回201；登录 `POST /api/auth/sign-in/email`，body `{email,password}` 返回200；两者均 `{user:{id,name,email},session:{id,userId,expiresAt}}`，session.id只是不可鉴权的摘要标识。退出 `POST /api/auth/sign-out`，body `{}` 返回200 `{success:true}`；会话 `GET /api/auth/get-session` 返回同样 `{session,user}` 或 `null`。密码12–128字符。退出使数据库会话立即失效。
- 业务 API 前缀 `/api/v1`。`GET /api/v1/me` 返回 `{user:{id,name,email}}`，未登录 401。
- 单资源直接返回对象；列表统一 `{items:[],total,limit,offset}`，分页 `?limit=50&offset=0`，limit 1–100。POST 创建为 201；异步创建运行为 202；更新为 200。
- 错误统一 `{error:{code,message,details?},requestId}`。401 `UNAUTHENTICATED`；不可见及不存在资源均 404 `NOT_FOUND`；400 `VALIDATION_ERROR`；409 `CONFLICT`；503 `RUNTIME_UNAVAILABLE`。`/api/auth/*` 使用 `{code,message}`。
- 写操作检查 Origin；开发允许 localhost与127.0.0.1的5173、4100端口，新增来源由 `E2E_TRUSTED_ORIGINS` JSON列表配置。CLI 可省略 Origin，但仍需会话 Cookie。
- 所有项目子资源、版本、运行、录制、工件均在服务器核对项目 owner，其他账号不能访问。初版每项目单 owner，无共享成员模型。

## 项目、组、场景与版本

`GET/POST /projects`；`GET/PATCH /projects/:projectId`。项目 `{id,name,description,ownerId,createdAt,updatedAt}`，创建/修改 `{name,description?}`。

`GET/POST /projects/:projectId/groups`；`GET/PATCH /projects/:projectId/groups/:groupId`。组 `{id,projectId,name,description,createdAt,updatedAt}`，创建/修改 `{name,description?}`。

`GET/POST /projects/:projectId/scenarios`（可 `?groupId=`）；`GET/PATCH /projects/:projectId/scenarios/:scenarioId`。场景 `{id,projectId,groupId:null|string,name,description,currentVersionId:null|string,createdAt,updatedAt}`，创建/修改 `{name,description?,groupId?}`。

`GET/POST /projects/:projectId/scenarios/:scenarioId/versions`；`GET /projects/:projectId/scenarios/:scenarioId/versions/:versionId`。新版本 body `{code,source:'human'|'ai'|'recording'|'import',changeNote,checks:[{id,title,kind:'ui'|'api'|'data',expected?}],modules?:{[name]:code}}`。代码是完整 Playwright Test TypeScript，import `{test,expect}` from `@playwright/test` 或平台 fixtures `/opt/runner/fixtures.ts`。模块名仅支持单层 `[a-zA-Z0-9_-]+.ts`，通过 `./modules/name` 引用。

版本返回 `{id,scenarioId,projectId,number,code,source,changeNote,checks,modules,createdAt,createdBy}`，只新增，不支持覆盖/删除。可复用流程按版本中的 modules 固化，避免历史运行被改写。空 checks 可保存，但执行若无真实断言必须显示 `unverified`。

## 环境

`GET/POST /projects/:projectId/environments`；`GET/PATCH /projects/:projectId/environments/:environmentId`。

输入 `{name,description?,websites:{main:'https://test.example.com',admin:'https://admin.test.example.com'},apiBases:{main:'https://api.test.example.com'},variables:{key:'value'},secretVariables?:{key:'secret'},roles:[{name,storageState?:{cookies:[],origins:[]},headers?:{}}],setup:[ApiAction],cleanup:[ApiAction],allowedOrigins?:string[]}`。`websites` 至少一个。`ApiAction` 为 `{name,apiBase,method:'GET'|'POST'|'PUT'|'PATCH'|'DELETE',path,headers?:{},body?:any,expectedStatus?:number,capture?:{variable:'json.path'}}`；path 必须相对所选命名 API 基址，支持 `{{variable}}` 插值，setup 动态值可传到场景和 cleanup。

响应附加 `{id,projectId,createdAt,updatedAt}`；不返回 storageState、role headers、secretVariables 的值，改为 `{roles:[{name,hasStorageState,hasHeaders}],secretVariableKeys:[]}`。PATCH 未传敏感字段则保留；同名 role 未传 storageState/headers 也保留。实际运行接收解密后的专属环境快照，API 展示快照仍脱敏。

通过API创建的action省略expectedStatus时默认200；创建/删除等端点应明确填201/204。body:null表示不发送请求体，显式false/0/空字符串仍作为请求体发送；capture:{}不解析响应JSON，适用于204或纯文本接口。

PATCH secretVariables逐key合并，`{}`不清空已有值；roles数组表达最终角色名单，同名role缺省凭据保留。setup/cleanup配置原样返回；敏感action headers（Authorization/Cookie/X-API-Key）必须使用 `{{secretVariable}}`，不接受literal凭据。不把脱敏占位写回配置。

数据库校验当前由 API action / Playwright request 完成，数据库连接保留适配边界，暂不支持直接连接数据库。

## 运行

`GET/POST /projects/:projectId/runs`；POST body `{environmentId,scenarioId?,groupId?,versionId?,role?:string,retries?:0..2,timeoutMs?:1000..600000}`。scenarioId 与 groupId 二选一；versionId 仅单场景可选，默认当前版本。创建时立即固化所有版本与完整环境快照，后续编辑不影响已排队或历史运行。

`GET /projects/:projectId/runs/:runId` 返回 `{id,projectId,environmentId,scenarioId:null|string,groupId:null|string,role:null|string,status,verification,createdAt,startedAt:null|string,finishedAt:null|string,timeoutMs,retries,environmentSnapshot,versions:[Version],summary,error:null|string}`。

`status`: `queued | running | passed | failed | cancelled | timed_out | error`。`verification`: `pending | verified | unverified | partial`。`summary` 含 `{total,passed,failed,skipped,flaky,unverified}`；失败重试后通过仍保留 attempt 与 `flaky`。`POST /.../runs/:runId/cancel`，body `{}`，返回运行；取消终态幂等。重跑再次 POST，得到独立 runId。

`POST /.../runs/:runId/rerun` body `{}`，202返回新Run：复制原运行所有版本和原环境快照（包括加密凭据），附加 `rerunOf`。原记录不改写；适用于单场景和测试组。普通POST /runs使用当前指定环境配置，rerun使用原快照。

`verification`来自实际expect步骤，不来自checks元数据；全部跳过或最后attempt零断言为unverified，部分跳过/零断言为partial。verified只说明实际执行了断言，不表示成功。零收集测试为error。详细字段与实际事件关联见 [runner.md](runner.md#实际事件结构)：stepId/parentStepId/testId/attempt关联操作与证据；首次失败与重试均保留。

`GET /.../runs/:runId/events?after=0&limit=100` 返回 `{items:[{seq,type,timestamp,data}],nextAfter}`，可轮询，每步、期望实际、stdout/stderr、前置/清理、测试 attempt 和最终结果持续持久化。`GET /.../runs/:runId/artifacts` 返回 `{items:[{id,runId,name,contentType,size,url,kind}],total,limit,offset}`。

事件例子（省略timestamp）：`{seq:12,type:'step.begin',data:{testId:'t1',attempt:0,stepId:7,parentStepId:6,title:'expect toHaveText',category:'expect'}}`；结束为同ID `step.end` 附加`status,durationMs,error?,expected?,actual?`。`assertion`含`testId,attempt,stepId?,matcher,negated,expected:args[],actual,status`；`test.end`含`testId,attempt,status,expectedStatus,assertions,errors,attachments`；`network`含`testId?,attempt?,phase,url,method?,status?,source?`；`run.finished`含`runId,status,verification,summary,error?`。attempt从0开始，首次失败与每次重试是不同attempt，不能按title覆盖。完整类型见 [runner.md](runner.md#实际事件结构)。

`GET /.../runs/:runId/artifacts/:artifactId` 下载私有文件。`GET /.../runs/:runId/trace` 打开后端同源托管的官方 Trace Viewer，仍须 Cookie 授权；无 trace 时返回明确 404。工件不暴露磁盘绝对路径、VNC 原始地址或公开链接。

## Web 录制

`GET/POST /projects/:projectId/recordings`；POST `{environmentId,website:'main',role?:string,scenarioId?:string}`，202 返回 `{id,projectId,environmentId,scenarioId:null|string,status:'starting'|'ready'|'stopped'|'error',createdAt,expiresAt,viewerUrl:null|string,code,checks:[],error:null|string}`。

`GET /projects/:projectId/recordings/:recordingId` 轮询；`POST /.../recordings/:recordingId/stop`，body `{}`，返回最终代码；`POST /.../recordings/:recordingId/save`，body `{scenarioId,changeNote}`，创建不可变 recording 来源版本，返回 Version。

save会先停止并等待codegen flush，只有成功stopped才保存版本；flush失败返回409 `RECORDING_FLUSH_FAILED`，不以旧轮询代码冒充最终录制。异常关闭导致target丢失时状态变error，不能仍显示ready。

`viewerUrl` 是后端授权的同源 noVNC 页面，可 iframe 嵌入。底层 WebSocket 连接也核对 session、project、recording，不提供公开 VNC 地址。复用官方 codegen Inspector：动作录制及可见/文本/值断言点选均在远程浏览器完成，不会要求本机客户端。

## 系统与联调

`GET /api/health` 存活检查。`GET /api/v1/capabilities`（需登录）返回版本和运行/录制依赖准备状态。`GET /api/openapi.json` 为可消费 OpenAPI。仅技术夹具用于验证，不绑定首个业务站点。

Git 提交标识源码版本；本机运行快照 `data/deployment-snapshot.json` 记录 API PID、捕获时间、核心源文件 SHA256 及 Docker 镜像 ID。快照仅保存在本地，两种记录均不能单独证明远端部署。Playwright 固定版本 1.63.0，业务 API 契约版本 1.0.0。

实现细节与新字段可向后兼容追加；删除/更名字段或改变状态前必须通知前端与管理任务。

补充业务错误code：400 `INVALID_ROLE`、`INVALID_WEBSITE`、`INVALID_TARGET`；409 `EMPTY_GROUP`、`MISSING_VERSION`、`RECORDING_LIMIT`、`RECORDING_NOT_READY`、`RECORDING_FLUSH_FAILED`、`EMPTY_RECORDING`；404 `TRACE_NOT_FOUND`；429 `RATE_LIMITED`。认证重复邮箱409 `USER_ALREADY_EXISTS`，错误账号密码401 `INVALID_CREDENTIALS`。
