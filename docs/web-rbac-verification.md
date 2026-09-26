# Web 组织、工作区与 RBAC 交付记录

## 结构与行为

Flowtest 直接在浏览器使用。Python、Node、Docker 与 Playwright 都是服务端运行依赖，录制浏览器通过原有同源 noVNC HTTP/WebSocket 代理操作，用户不用安装客户端。

组织是成员与权限边界，组织内工作区对该组织成员共享访问，项目归属工作区。权限只维护一套组织角色：owner、admin、member、viewer。服务端对所有项目资源、环境、运行、工件、Trace 和录制入口检查组织成员资格；录制浏览器控制要求编辑权限。不存在个人创建者绕过组织角色的授权逻辑。

新账号可创建组织（同时创建默认工作区），或通过邀请加入。邀请绑定账号邮箱，链接为前端 `/join#token=...`，仅创建时返回原始token，数据库只保存SHA-256。预览与接受使用POST请求体。接受邀请、撤销、成员管理及最后owner检查由组织行锁串行化，并有数据库成员主键与待接受邀请唯一索引保护。

项目响应现在包含organizationId、workspaceId、createdBy，删除ownerId；项目列表和创建必须指定workspaceId。完整API、权限矩阵与错误码见 [api-contract.md](api-contract.md)。邀请有效期7天，邀请者退出不会自动撤销已发邀请，组织管理员可显式撤销；接受后再次访问不创建重复成员，退出后不能复用旧邀请。

## 迁移与本地运行

`0003_organizations.py` 已在本地PostgreSQL应用：为有个人项目的原账号创建组织及默认工作区，原账号成为owner，项目及全部子资源ID保留。项目JSON去掉ownerId并补充新归属，历史录制补充原创建者。不可变版本、密文环境和执行证据不重写。

迁移前备份在本地 `data/rbac-before-0003.dump`，不提交。旧单owner模型不能表达多人组织，降级不会静默删除成员关系；回退需恢复迁移前备份。

本地API仍为 `http://127.0.0.1:4100`，前端为5173；启动时设置 `E2E_APP_URL=http://127.0.0.1:5173`，邀请链接指向前端。技术夹具仍为18080/18081。公网配置与HTTPS同源代理方式见 [self-hosting.md](self-hosting.md)，本轮没有部署公网服务。

## 验证结果

- `uv run pytest -q`：29项通过（45.61秒），真实PostgreSQL Schema与迁移。包括原测试流程、录制并发，以及新组织/工作区隔离、跨租户404、viewer写403、私有证据只读访问、邀请邮箱绑定、过期/撤销/替代、并发接受、重复接受、旧邀请不可重新加入、admin不可提升权限、最后owner并发退出保护。
- 真实WebSocket测试验证viewer握手被拒绝；提升member后可转发数据；移除成员后连接在复验周期内断开。上游只收到服务器网关凭据，没有平台会话Cookie。
- HTTPS配置测试验证Secure、HttpOnly、SameSite=Lax Cookie，DELETE预检与凭据CORS，以及跨站写入拒绝。
- `scripts/acceptance.py` 新租户链路通过：运行 `8588b71a-4ea3-4860-b550-80bffe6eb21f`、原快照重跑 `fe5e4428-c187-46cc-879c-6b5e62c02382`、切换环境运行 `a1c44873-06b0-49bb-bf2e-9e14ae453a7e` 均passed/verified，各3份私有工件；包括录制HTTP/RFB授权、停止及命名环境绑定。报告在本地 `data/api-runtime-acceptance.json`，上一轮报告保留为 `data/api-runtime-acceptance-pre-rbac.json`。
- 已只读核验上一轮的3条历史run迁移后仍为passed/verified，项目workspace关联正确、原创建者为组织owner，旧ownerId已移除。
- Ruff、格式检查、OpenAPI生成一致性与差异检查通过。当前OpenAPI为40条路径、57项操作。

前端会话独立负责浏览器设计、导航和多用户UI验收，其报告由前端项目维护；不把前端反馈等同于本文件列出的后端自动化证据。最后确认的测试日志为 `data/rbac-final-tests.log`、`data/rbac-http-acceptance.log`，执行记录和历史失败均保留。

## 约束与边界

组织与工作区没有删除接口；当前主路径包括创建、重命名、切换、邀请加入和成员管理。工作区不单独设置成员，项目不支持跨组织移动。本轮未扩展SSO、邮件投递或付费，也没有验证公网TLS部署、压力或长时间运行。日志中的租户业务结果在提交后记录，不输出邀请token、原始邮箱或请求体；诊断日志不替代持久化事实。
