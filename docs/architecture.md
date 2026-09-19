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
