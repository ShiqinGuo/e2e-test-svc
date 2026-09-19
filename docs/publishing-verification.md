# GitHub 公开发布检查

日期：2026-09-20。基线：`a3ec836`。本次范围为 README、功能动画、架构图、发现入口与 MIT 许可，不修改 API、运行器或录制器实现。

## 本次实际执行

| 检查 | 结果 |
| --- | --- |
| `npm run build` | TypeScript 检查通过 |
| `node --import tsx --test tests/recorder.test.ts` | 6 passed；录制源码绑定、未知地址、断言和秘密引用 |
| `uv run python scripts/openapi.py --check` | 通过；30 paths / 41 operations |
| `uv run ruff check app migrations scripts tests/test_api.py docs/media/render.py` | 通过 |
| `uv lock --check` | 通过；未改变运行依赖 |
| README、英文页、开发指南、架构和媒体说明的本地文件链接 | 均存在 |
| GIF 与 SVG | GIF 288 帧、1120×640、约 25.7 秒；逐帧解码通过；架构 SVG XML 可解析并经浏览器渲染检查 |
| 媒体目视检查 | 录制、快照、失败与重试的关键画面，以及架构图均检查；保留静态图替代 |
| 公开范围初检 | 检查既有 Git 历史的 76 个文本 blob：未发现私钥或所检查的常见服务 token 格式；无被跟踪的 `.env`、`data/` 或私钥文件 |
| 许可 | 自有代码 MIT；npm 包与锁文件元数据一致；第三方许可保持原状 |

凭据格式检查范围有限，不是完整安全审计。动画只含合成示例；README 使用的前端工作台截图来自已有技术夹具验收。

本次没有重新运行 PostgreSQL 集成、Docker 执行/录制或全量 Web E2E，也没有重启正在运行的后端、容器或前端。过去真实运行的事实和原始失败记录保留在 [acceptance.md](acceptance.md)，不能把本次文档检查当作新一次完整运行验收。

GitHub 发布说明应指向本次实际提交；源代码发布不表示已经完成远端部署。
