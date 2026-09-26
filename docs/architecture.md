# 技术架构与源码对应

本图描述当前代码的组件边界，不是未来规划。平台数据库和被测业务数据库是不同概念：Flowtest 当前通过被测 API 校验业务数据，未实现多数据库直连。

![Flowtest architecture](media/architecture.svg)

| 组件 | 职责 | 源码 |
| --- | --- | --- |
| React Web 工作台 | 源码与检查点、版本、环境和运行证据 | [App](https://github.com/ShiqinGuo/e2e-test-fronted/blob/main/src/App.tsx)、[API client](https://github.com/ShiqinGuo/e2e-test-fronted/blob/main/src/api.ts)、[RunWorkspace](https://github.com/ShiqinGuo/e2e-test-fronted/blob/main/src/runs/RunWorkspace.tsx) |
| FastAPI 控制层 | Cookie 会话、项目授权、录制代理与运行编排 | [main.py](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/app/main.py)、[auth.py](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/app/auth.py)、[jobs.py](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/app/jobs.py) |
| PostgreSQL | 用户与会话、版本、资源、运行及事件等持久状态 | [models.py](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/app/models.py)、[store.py](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/app/store.py) |
| Node 控制器 | 受信任的工具进程，通过 JSON stdio 与 Python 通信 | [runtime.py](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/app/runtime.py)、[bridge.ts](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/runtime/bridge.ts) |
| 录制器 | 独立容器中的 Chromium、官方 codegen / Inspector、noVNC | [recorder.ts](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/src/runtime/recorder.ts)、[recorder runtime](https://github.com/ShiqinGuo/e2e-test-svc/tree/main/runtime/recorder) |
| 运行器 | 执行固化版本，输出逐尝试事件和工件 | [runner.ts](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/src/runtime/runner.ts)、[reporter](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/runtime/runner/reporter.mjs) |

导入的场景代码在 Docker 运行器内执行，不在 API 进程执行。录制器页面、WebSocket、工件和 Trace 入口由 API 校验会话与项目归属；图中连接表示逻辑职责，不能理解为公开的容器端口。

场景版本只新增；创建运行时固化源码、模块与环境。历史重跑复制原始快照并产生新运行，不回写旧记录。断言依据真实 reporter 事件；检查点说明元数据不能证明实际断言执行。首次失败、后续重试、跳过和未验证结果分别保留。

详细契约见 [API contract](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/docs/api-contract.md)，执行与网络边界见 [runner](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/docs/runner.md) 和 [self-hosting](https://github.com/ShiqinGuo/e2e-test-svc/blob/main/docs/self-hosting.md)。

## Python 控制服务结构

- `app/main.py` 只负责依赖组装、生命周期和 Router 注册；`context.py` 提供 HTTP / WebSocket 共用的具名依赖。
- `app/routes/` 按项目、场景、环境、运行、工件、录制及代理分开，负责协议与项目授权。简单 CRUD 保留直接表达，不增加透传 Service。
- `app/services/` 承担版本创建、环境合并、运行快照和录制状态编排；`jobs.py` 管理执行任务及中断恢复。外部控制器调用前释放数据库事务。
- `schemas.py` 定义输入；`responses.py` 定义可公开的响应字段；`openapi.py` 由可执行模型生成契约，`docs/openapi.json` 是生成产物，不再作为生成器的输入。
- `domain.py` 集中定义资源种类、运行与录制各自的状态及迁移规则；`errors.py` 集中管理错误码、HTTP 状态和默认消息。
- `models.py` / `store.py` 使用 SQLAlchemy 持久化。JSONB 保留环境与运行快照结构；版本的唯一序号和不可变触发器继续由 PostgreSQL 保证。迁移 `0002` 增加资源种类、状态约束并去掉 `sourceRunId`，已发布 `0001` 保留原样。
- `runtime.py` 是受信任 Node 控制器的协议适配器，`RuntimePort` 明确应用所需操作。运行和录制结果经过边界校验，畸形结果进入明确失败，不能补默认值制造成功。
- `observability.py` 使用 OpenTelemetry 的 FastAPI、SQLAlchemy、HTTPX 自动埋点，输出关联 request / trace / span 的请求摘要及提交后业务事件。默认不采集请求体、响应体或认证头；本项目没有配置远端 Trace exporter，日志可本地关联，远端采集需独立配置。

录制停止先在短事务中将状态改为 `stopping`，释放行锁，再由受管理的后台任务完成 codegen flush。并发停止不会再次调用控制器；客户端断开不会取消 flush；轮询旧结果不能覆盖最终代码。只有 `stopped` 可以生成版本。失败及进程中断保留为 `error`。

本轮重构与验证记录见 [refactor-verification.md](refactor-verification.md)。

## Web 多租户协作

`models.py` 中 Organization、Membership、Workspace、Invitation 是独立的类型化表。`permissions.py` 统一组织角色及资源访问判断；`services/tenants.py` 处理成员与邀请事务；`routes/tenants.py` 提供组织/工作区/成员/邀请接口。项目通过workspace归属组织，created_by仅保留创建者事实，不赋予权限。

组织角色控制整个工作区：viewer读取，member编辑测试和操作运行/录制，admin管理工作区及普通成员，owner管理组织与高级身份。所有成员变化和邀请接受先锁组织，避免并发移除最后owner或重复加入。录制WebSocket复验会话和成员权限；Trace与工件维持只读私有访问。迁移、部署和验证记录见 [web-rbac-verification.md](web-rbac-verification.md)。
