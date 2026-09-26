# 后端工程重构记录

本文记录引入组织协作之前的第一轮工程重构。之后的实体、权限、迁移和当前验证数量见 [Web RBAC记录](web-rbac-verification.md)，下文保留当时的验证事实。

## 改动与取舍

按根目录 `AGENTS.md` 的可读性、边界校验、Fail-fast 和不保留兼容实现原则，完成 Python 控制服务的职责拆分、依赖组装、输入/输出契约、状态与错误管理、事务边界、运行时协议和可观测性调整。前端根目录另有同风格工程约定，并同步修改录制停止状态与重跑来源字段。

保留 PostgreSQL JSONB 快照、不可变版本触发器、FastAPI Users 会话和 Docker 执行隔离；没有为统一分层创建通用基类、空 Repository 或替换成熟运行器。Node 的 Playwright 控制器与沙箱继续作为受信任外部适配层，执行语义由原有 Docker 集成测试核验。

OpenAPI 由实际 Pydantic 输入与响应生成，公开响应采用字段白名单。移除手工响应 Schema 合并及 `sourceRunId` 别名；新迁移为 `0002_resource_contracts.py`，已执行的 `0001` 不改写。迁移前本地 PostgreSQL 备份保留在 `data/refactor-before-0002.dump`，属于含账号数据的本地备份，不提交。

## 验证证据

- PostgreSQL 回归：21 项通过，包含原有权限、快照、版本、事件、工件和录制一致性，以及新增事务释放、停止并发、断连继续 flush、畸形运行结果、日志提交语义、数据库状态约束和旧快照迁移。
- Node 类型构建通过；启用两组 Docker 开关后，10 项测试全部通过，无跳过，覆盖真实 Chromium、API、重试、取消、超时、录制网关、启动中停止和凭据清理。
- 前端构建通过，38 项单元测试通过。构建保留现有的大 chunk 提示；本轮未将该提示当作性能瓶颈重构。
- 实际 HTTP → PostgreSQL → Node → Docker → Chromium 验收通过：原环境运行 `00b9fc50-3f1c-4934-987f-3d63d08c7129`、原快照重跑 `6f94d6c6-005b-4f5a-bd98-f05cdbe828f5`、新环境运行 `9392c7d6-c089-4deb-a5a4-7f9d28e08404` 均为 passed / verified，各保留3份私有工件。录制页面授权、VNC 代理、停止与保存也由该脚本验证。报告：本地 `data/api-runtime-acceptance.json`。
- 前端 `npx playwright test e2e/runs.spec.ts`：1项通过（26.9秒），覆盖真实运行证据、混合结果、私有 Trace、原快照重跑与取消。截图及JSON报告写入本次 `test-results`，不覆盖历史文档截图。
- Ruff、格式检查与 OpenAPI 一致性检查通过；生成契约仍为30条路径、41项操作。两份后端AGENTS文件的SHA-256相同：`25e2aca4aba859f6beaa97d0aa20a8932f0c40e366f8b1ad2af9a237d9efb427`。

运行命令：`uv run ruff check .`、`uv run python scripts/openapi.py --check`、`uv run pytest -q`、`npm run build`；Docker 测试设置 `E2E_RUN_DOCKER_TESTS=1` 与 `RUN_DOCKER_TESTS=1` 后执行 `npm test`。

首轮失败没有隐藏：OpenAPI 测试引用旧 `Run` 模型名，改为沿接口实际 `$ref` 验证快照字段；下一轮暴露生成器的 Cookie 安全方案大小写改变，恢复 `sessionCookie`。新增数据库回滚测试曾访问过期 ORM 对象而产生 `MissingGreenlet`，改为在回滚前保存身份再显式查询。业务预期未放宽，最终21项通过。原始新增测试失败与修复后记录分别保留在本地 `data/refactor-pytest.log`、`data/refactor-pytest-2.log`。

## 边界

本轮是本机开发与技术夹具验收，未发布远端服务，也不代表真实业务站点或长时间压力测试。自动埋点和本地关联日志已接入；没有配置、也未声称验证远端 Trace 采集。日志事件是诊断记录，运行事件和不可变版本仍以数据库为事实来源。
