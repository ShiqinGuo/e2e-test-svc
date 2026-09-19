> 从原 README 保留的开发与运行指南。以下命令均在仓库根目录执行。产品入口见 [README](../README.md)。

# Web 业务流程自动化测试后端

Python + FastAPI + PostgreSQL 管理账号、项目、不可变场景版本、环境快照与持续运行记录。独立 Docker 容器复用 Playwright Test、codegen Inspector 和 noVNC，导入代码不会在 API 进程执行。前端项目独立，本目录不包含桌面客户端，也不部署被测业务应用。

前端仓库：[e2e-test-fronted](https://github.com/ShiqinGuo/e2e-test-fronted)。

## 本地启动（Windows / Linux）

依赖：Python 3.12+、uv、Node 24+、npm、Docker Linux engine。当前锁文件在 Python 3.13、Node 24 上验证。

```powershell
uv sync --frozen
npm ci
uv run python scripts/bootstrap.py
docker compose up -d --wait postgres
uv run python -m app.migrations
docker build -f runtime/runner/Dockerfile -t e2e-runner:1.63.0 .
docker build -f runtime/recorder/Dockerfile -t e2e-recorder:1.63.0 .
uv run uvicorn app.main:app --host 127.0.0.1 --port 4100 --no-access-log
```

`bootstrap.py` 只创建尚不存在的 `.env` 和 `data/postgres.password`，密码不会输出。Compose 项目名 `e2e-test-platform`，独立 PostgreSQL 18.6 容器、卷和本机端口 55432，不使用其他项目的数据库。迁移由 Alembic 管理，不在服务启动时无条件建表。

后端 [health](http://localhost:4100/api/health)、[OpenAPI](http://localhost:4100/api/openapi.json)、[接口文档](api-contract.md)。开发前端将 `/api` 同源代理到4100并开启 WebSocket，允许 `localhost:5173` 与 `127.0.0.1:5173` 的 Origin。账号通过前端注册，服务不预置共享管理员或演示登录。

请运行单个 Uvicorn worker；服务持有 PostgreSQL advisory lock 阻止多个编排进程竞争。Windows 上运行器需要支持 subprocess 的事件循环，推荐上述不带 `--reload` 的启动方式。修改后端后受控重启即可，账号与资源保留。中断的活动运行明确标记 error，不会补写成功；遗留运行容器按此数据库的运行 ID 清理。

## 场景闭环

1. 注册并创建项目、业务测试组、场景和测试环境。一个环境可包含多个网站、API 基址、角色会话、公用与秘密变量、API 数据准备及清理。
2. 在 Web 工作台开始录制，直接操作远程 Chromium；Inspector 的可见、文本、值断言点选由上游 Playwright 实现。停止并保存生成不可变版本。也可导入人或 AI 编写的 Playwright Test TypeScript，无需付费模型账户。
3. 运行场景或组。创建时固化版本、模块和环境；运行中事件、步骤、断言期望/实际、重试、日志、截图与 Trace 持续记录。`checks` 是可读说明，是否执行了断言以真实 reporter 事件为准。
4. 历史“重新执行”调用 `/runs/:runId/rerun`，复制原始快照并创建独立记录；使用当前环境由普通新建运行入口明确发起。

可复用流程使用版本内 `modules`（如 `checkout.ts`），场景通过 `./modules/checkout` 引用。动态数据使用 `platform.get/set` 和 API action `capture`。完整例子见 [运行说明](runner.md)。数据库校验当前优先 API，未接多数据库直连适配。

最小CLI入口：`uv run python scripts/cli.py --project PROJECT_ID --scenario SCENARIO_ID --environment ENVIRONMENT_ID`，按交互提示登录；CI可以用受限本地 `--cookie-file PATH`，文件中保存会话Cookie值，凭据不放命令参数。`--rerun RUN_ID` 复用原快照。CLI仅在passed、verified且无flaky时返回0，未验证/跳过/失败不会默默通过CI。

## 验证

```powershell
uv run pytest -q
uv run ruff check app migrations scripts tests/test_api.py
npm run build
uv run python scripts/openapi.py --check
node --import tsx --test tests/recorder.test.ts
$env:E2E_RUN_DOCKER_TESTS='1'
node --import tsx --test tests/runner.integration.test.ts
$env:RUN_DOCKER_TESTS='1'
node --import tsx --test tests/recorder.integration.test.ts
```

API 测试在当前 PostgreSQL 里建立并清理独立 `test_<uuid>` schema，真实验证迁移、会话、项目权限、快照、工件和契约，不以 SQLite 替代 PostgreSQL。测试账户、运行和浏览器使用技术夹具，尚未接入首个真实业务网站。

共享技术夹具启动：

```powershell
docker run -d --name e2e-demo-fixture --label e2e.fixture=shared -p 127.0.0.1:18080:8080 -p 127.0.0.1:18081:8081 --entrypoint node e2e-runner:1.63.0 /opt/runner/fixture-server.mjs
uv run python scripts/acceptance.py
```

已有同名夹具时不用重复创建。用户浏览 `http://localhost:18080/`；worker 网站与API填写 `http://host.docker.internal:18080/`，18081为环境B。Linux Docker需把主机可达地址或夹具所在内网地址配置为目标；`host.docker.internal`在本机Docker Desktop已验证。

技术夹具 Order 输入与 Submit order 按钮会创建真实内存订单，页面显示订单 ID，`GET /orders/:id` 查询、`DELETE /orders/:id` 清理。数据只供技术验收，重启夹具清空。`scripts/acceptance.py` 通过真实 API 创建隔离账号，验证 UI/API 同实体、数据准备清理、历史快照重跑、切换环境、私有工件和授权录制 WebSocket。凭据不写进验收报告。

## 自托管和证据边界

见 [自托管配置](self-hosting.md)、[API 与权限验收](acceptance.md)、[录制适配](recorder.md)。实际通过项和未验证边界以这些文档及验收产物为准，单测或容器小验证不是完整前端 UI 验收。

Git 提交标识源码版本；本地 `data/deployment-snapshot.json` 记录实际服务的 API PID、核心源文件 SHA256 和镜像 ID，两者分别用于源码追踪和本机运行核验，均不能单独证明远端部署。更新服务后可用 `uv run python scripts/deployment_snapshot.py --pid ACTUAL_UVICORN_PID` 刷新运行快照。

本地凭据、`data/`、数据库、日志和验收截图/JSON 不提交到 Git。`runtime/recorder/evidence/` 中保留的两份 TypeScript 是录制绑定回归测试的固定输入；同目录截图与 JSON 由对应验收脚本在本地生成。

`data/`、`.env`、PostgreSQL 数据卷和加密密钥需要持久保存；环境凭据用 Fernet 加密，密钥保留在 API 宿主，运行器只收到该次执行需要的快照。工件为项目私有，会脱敏已知秘密和认证字段；页面截图中的任意敏感文字没有通用自动识别能力。录制凭据应预先放入秘密变量或角色会话；未知秘密不会凭空获得变量绑定。

复用组件采用各自开源许可证；未复制 WrightTest 受限代码。认证源自 [FastAPI Users](https://fastapi-users.github.io/fastapi-users/latest/configuration/authentication/strategies/database/)，浏览器与报告使用 [Playwright](https://playwright.dev/docs/docker)，桌面传输使用 [noVNC](https://github.com/novnc/noVNC)。
