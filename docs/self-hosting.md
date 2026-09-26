# 自托管部署

推荐在一台受控 Linux 主机上运行 Python API 和 Docker Linux。先按 README 准备 PostgreSQL、迁移与两个固定版本运行镜像，再用进程管理器启动 `uv run uvicorn app.main:app --host 127.0.0.1 --port 4100 --no-access-log`。API账号需要操作本机Docker；导入的场景代码只在独立容器内执行，没有Docker socket、平台数据库、平台会话或其他项目的文件挂载。

配置：

| 参数 | 用途 |
| --- | --- |
| `E2E_DATABASE_URL` | PostgreSQL SQLAlchemy asyncpg DSN；从受限本地配置文件读取 |
| `E2E_DATA_DIR` | 密钥、运行和录制工件的绝对持久化目录 |
| `E2E_BASE_URL` | 用户访问的HTTPS平台地址 |
| `E2E_APP_URL` | 前端公开地址，用于邀请链接；本地为 http://localhost:5173，生产为同源HTTPS地址 |
| `E2E_COOKIE_SECURE` | HTTPS部署设置true |
| `E2E_TRUSTED_ORIGINS` | JSON数组，明确允许的平台前端Origin |
| `E2E_MAX_CONCURRENT_RUNS` | 同时执行的运行数，默认2 |
| `E2E_MAX_RECORDINGS_PER_USER` | 每账号同时录制数，默认2 |
| `E2E_RECORDING_LIFETIME` | 录制会话寿命秒数，默认1800 |
| `E2E_SESSION_LIFETIME` | 数据库登录会话寿命秒数，默认604800 |

前端静态资源与 `/api` 应放在同一个 HTTPS 域名。反向代理需支持 WebSocket，并对 `/api` 保留 Cookie 与 Origin。示例 Nginx片段（TLS证书配置由部署方提供）：

终端用户只需浏览器，无需安装 Python、Node、Docker、Playwright 或桌面客户端。这些运行依赖只安装在服务器。录制器通过同源HTTP/WebSocket网关访问，远端容器地址和网关凭据不发给浏览器。公开站点的前端与API使用同一HTTPS域名，设置 `E2E_COOKIE_SECURE=true` 并将该域名加入 `E2E_TRUSTED_ORIGINS`；代理 `/api` 的 GET/POST/PATCH/DELETE 与 WebSocket。

迁移 `0003` 把每位原项目owner的存量项目放入该用户的组织及默认工作区，保留全部项目/场景/版本/运行ID。先备份PostgreSQL与加密密钥，再停止旧API，运行 `uv run python -m app.migrations`，同时更新前后端。该版本不提供旧个人项目API兼容层；组织成员关系无法无损退回旧单owner结构，回退需要恢复迁移前备份。

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}
server {
    listen 443 ssl;
    server_name testing.example.com;
    root /srv/e2e-frontend/dist;
    client_max_body_size 2m;
    location / { try_files $uri /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:4100;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1900s;
        proxy_buffering off;
    }
}
```

平台与被测应用应使用不同网络端点；worker代理明确拒绝平台Origin、平台端口与控制端口。命名网站/API和allowedOrigins决定运行器可访问的目的地。允许显式配置内网测试环境；没有自动部署被测业务站点的能力。HTTPS CONNECT只验证端点，平台不能与目标共用同一物理IP+443端点后再依赖Host变化区分权限。

停止服务时先让当前运行完成或在平台取消；关闭会终止活动容器。主机崩溃或强杀后，启动恢复会保留中断事实为error，不伪造重试通过，并按本数据库的运行/录制ID清理遗留容器和秘密输入文件。Docker不可用时会在错误说明保留清理失败，管理员恢复Docker后处理对应标签资源。数据库备份必须与 `data/encryption.key` 一起保存；没有密钥不能解密历史环境快照。备份/恢复流程未在远端部署演练，当前证明范围是本机真实PostgreSQL和Docker集成。

历史版本和运行不提供删除API。组织成员与工作区共享已实现，所有私有资源按组织角色授权；尚未提供多API进程分布式队列、邮件验证/找回密码，以及完整工件保留策略。邀请链接不需要邮件服务。容量清理由管理员在停机备份后按实际保留需求实施，不能删失败记录来改变运行结论。
