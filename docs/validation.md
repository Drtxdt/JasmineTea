# 验证记录

更新日期：2026-10-10。这个记录区分离线实现验证、真实平台调用和实际客户端运行。

## 0.2 本轮验证

- Windows Python 3.13.1 / Git 2.45.1：97 项自动测试、Ruff 通过。覆盖空文本、query 契约、缓存恢复、流式搜索及回收、登录身份、两种协议的工具注册、分叉历史、真实远端保护拒绝、写入超时和 HTTP/Git 代理开关及 NO_PROXY。
- 专用 bdgit 闭环通过：标识 `79cf37d74f6e`，Issue #7 关闭，PR #8 合并，两条临时分支删除；本地记录 `artifacts/live-79cf37d74f6e.json`。未改动原默认分支。
- 标准 stdio 真实平台调用通过：68 个工具、读取、受控 Issue 写入、越权拒绝、清理；Issue #9 关闭，记录 `artifacts/live-stdio.json`。
- Windows/Linux × Python 3.11/3.13 和新增 Git 2.34.1 CI 的最终结果待推送后核对。
- 下面保留 0.1 的历史验证记录，不能代替本轮结果。

## 已验证

- Windows，Python 3.13.1，Git 2.45.1.windows.1，MCP Python SDK 2.3.0。
- 55 项自动测试通过。API 测试使用 bdgit 公开 Swagger 快照和模拟 HTTP 响应，校验所有原生工具对应的路径、请求 JSON Schema、错误、分页和权限检查。
- GitHub Actions 首次运行的 Windows/Linux × Python 3.11/3.13 四组任务均通过 Ruff 和 55 项测试；失败发生在后续打包步骤。[首次 CI](https://github.com/Drtxdt/JasmineTea/actions/runs/37956470046)
- CI 修复后的提交 `cecfc27`：Windows/Linux × Python 3.11/3.13 四组任务全部成功，覆盖 Ruff、55 项测试、隔离构建、Twine 和全新环境的 wheel 安装及两项 stdio 验证。[通过的 CI](https://github.com/Drtxdt/JasmineTea/actions/runs/37959688703)
- 真实本地 bare Git：多文件提交、对象生命周期、SHA lease、拒绝历史重写、分支创建、清理冲突、中文路径、二进制 Diff、重命名与 PR merge-base。
- MCP 原生协议及 legacy 模式：分别通过进程内和真实 stdio 子进程完成工具发现、结构化读取、受控写入、越权拒绝和不支持能力报错。
- 68 个 MCP 工具入口，89 项参考能力完整归类。当前 Swagger 对应 70 项 API/Git/等价实现，19 项不支持，其中包括缺少锁定字段的两个入口。这里的支持数不是实时平台实测数。
- Ruff、编译检查、wheel/sdist 构建与 Twine 检查通过。
- 新建独立虚拟环境，从 wheel 安装全部运行依赖，检查版本、包内能力矩阵和真实 stdio 的新旧协议读写。
- wheel 与 sdist 检查：没有 `.env`、虚拟环境、缓存或运行日志等私有文件。
- 2026-10-10 本轮本地检查：55 项测试、Ruff、隔离构建与 Twine 通过；`scripts/check_wheel.py` 从源码目录外的新虚拟环境验证安装、包路径、版本与能力表，两项 stdio 新旧协议测试通过。README 和安装文档的本地链接、待提交文件的凭据扫描也通过。
- 从本轮 sdist 解包到临时目录，新建独立虚拟环境并执行源码安装，版本 `0.1.0` 与 Codex 绝对路径配置生成验证通过。打包文件不含真实 Token、`.env`、缓存或运行记录；源码包包含新的 Agent 安装文档。
- Codex TOML、Claude JSON、DeepSeek Cordis 配置生成测试通过；凭据仅使用变量引用或本地文件。
- 真实 bdgit 鉴权：账号 `hndx202426010302`，测试仓库具有 admin/push/pull 权限；API 与 Git 连通性检查通过。
- 真实协作闭环复验通过：创建两条隔离分支、多文件提交、中文空格路径读取、创建 Issue 和 PR、评论、COMMENT 审查、合并到测试基准分支。Issue #3 已关闭、PR #4 已合并，两条临时分支均已删除；记录在本地忽略的 `artifacts/live-eb14b749a064.json`。
- 真实 stdio 服务进程：发现 68 个工具、查询 89 项能力、读取平台文件、创建 Issue #6 与评论、拒绝非白名单仓库写入、关闭测试 Issue；记录在 `artifacts/live-stdio.json`。SDK legacy 模式通过，不代表三个产品客户端已联调。
- 初始空仓库测试的闭环也通过（Issue #1、PR #2），但 Gitea 自动将首个基准分支设为默认分支，拒绝删除。保留 `jasminetea-test/base-9351dc10074a` 作为仓库唯一初始基准；原定 `main` 没有创建或修改。首次 stdio 验证脚本参数错误遗留的 Issue #5 已核对唯一标记并关闭。

## 尚未验证

- **三个客户端的真实平台调用**：当前只验证标准 MCP 和配置生成。已发现 Codex CLI 0.162.0、Claude Code 2.1.195；没有发现 `dsh` 命令。这些版本信息不代表已经完成客户端联调。
- **TestPyPI/PyPI 上传**：未执行。当前交付本地可安装的 wheel 与源码包。

## 后续平台验收

在项目启动目录或配置文件目录的 `.env` 中设置 `BDGIT_TOKEN`，不要把值发送到聊天、提交到 Git 或放进配置样例。然后使用安装本包的 Python：

```powershell
python -m jasminetea_mcp doctor --config examples/test-repository.toml --repo hndx202426010302/JasmineTea-test
python scripts/live_acceptance.py --config examples/test-repository.toml
```

验收脚本仅允许指定 bdgit 实例和测试仓库。空仓库会在隔离分支创建初始提交；平台若将它设为默认分支，清理时保留并明确记录。脚本生成唯一 `jasminetea-test/` 分支与 Issue/PR 标记，合并到测试基准分支，并把记录保存到忽略的 `artifacts/`。清理其他分支使用精确 SHA lease；发现额外提交会保留分支并报告清理失败。Issue/PR 保留为已关闭或已合并对象，便于审计。真实 stdio 验证使用 `python scripts/live_stdio.py`。

还需分别在三个客户端完成读取和受控写入；其余声明支持的 API 也需逐项真实验证。完整协作 MCP 计划的验收条件尚未全部达成。
