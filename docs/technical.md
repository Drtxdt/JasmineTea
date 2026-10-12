# 技术说明

## 配置与凭据

配置使用 TOML，默认平台为 `https://bdgit.educoder.net`，默认 Token 变量名为 `BDGIT_TOKEN`，可写仓库列表默认为空。

服务先读取配置，再寻找凭据。非空环境变量优先；否则只读取配置目录或启动目录 `.env` 中的指定变量。不会加载其他 `.env` 变量。建议把配置和 `.env` 放在同一私人目录，客户端使用配置的绝对路径。

API 使用 `Authorization: token ...`。API 和 Git 的写操作共用可写名单，工具调用不能修改它。名单只是本地限制，不能赋予账号在平台上没有的权限。

## Git 与 SHA

服务在用户缓存目录中使用隔离的 bare 仓库，不 checkout、不执行 hooks、不运行仓库代码，也没有任意命令工具。`cache_dir` 可更改位置；文件锁协调多个进程的访问。缓存按平台、仓库和凭据隔离。

Git 凭据通过进程环境传递，不写入远端 URL、命令参数或仓库配置；不将 Git 的原始错误输出返回给模型。

`create_blob`、`create_tree`、`create_commit` 创建本地对象，返回 SHA 供后续操作使用。`update_ref` 发布提交；这与 GitHub 在服务器上存储对象的方式不同。

`update_ref` 和 `commit_files` 要求当前分支完整的 `expected_sha`，使用原子 lease，并拒绝非 fast-forward 更新。单文件 API 的 `update_file`、`delete_file` 要求的是当前 **Blob SHA**，不是分支 SHA。多文件提交使用分支 SHA。

## 分页、输出与错误

分页默认 50 条，上限 100 条，返回 `next_page` 时可以继续读取。目录也在服务端分页。`read_file` 支持行范围，拒绝将二进制当成文本读取。

Diff 默认最多 512 KiB；HTTP 响应最多 16 MiB；单次工具输出最多 1 MiB。结果包含截断提示，超限时缩小页、路径或行范围。

只读请求可有限重试，写请求不自动重试。`result_uncertain=true` 表示结果尚未确认，先查询远端分支或对象状态，再决定是否重新操作。

`get_capabilities` 根据实例 Swagger 检查接口与字段。能力标记可用不代表当前账号已经实测成功，`doctor` 另行检查鉴权、仓库权限和 Git 连通性。

## 开发与打包

在源码目录创建虚拟环境后，使用该环境的 Python（Windows 是 `.venv\Scripts\python.exe`，Linux 是 `.venv/bin/python`）：

```sh
python -m pip install -e ".[dev]"
python -m ruff check src tests scripts
python -m pytest -q
python -m build
python -m twine check dist/*
python scripts/check_wheel.py
```

`python -m build` 会在隔离环境安装 Hatchling。`pip install -e .` 的构建依赖也在临时环境中，不代表 Hatchling 已安装到开发环境；不要在未安装后端的环境使用 `--no-isolation`。

`check_wheel.py` 新建不继承系统包的虚拟环境，从 wheel 安装运行依赖，并从源码目录之外验证包路径、版本、能力表及真实 stdio 新旧协议。测试使用本地模拟平台和虚假 Token，不访问 bdgit。

CI 包含 Windows/Linux 和 Python 3.11/3.13。真实平台测试需要个人 Token，使用 `scripts/live_acceptance.py` 或 `scripts/live_stdio.py`；它们会写入专用测试仓库，不用于普通安装验证。结果见[验证记录](validation.md)。

本项目尚未上传 TestPyPI/PyPI。本次只构建并检查本地包。

## 接口文档

- [bdgit Swagger](https://bdgit.educoder.net/swagger.v1.json)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)
- [DeepSeek Harness MCP](https://deepseek-harness.github.io/deepseek-harness/en/guide/mcp-memory)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)

## 0.2 行为与迁移（历史记录）

可写名单为空时只注册 37 个读取工具，非空时注册 68 个工具；修改配置后重启服务。能力报告仍覆盖 89 项，`registered_tools` 是本次实际注册列表。

`create_commit`、`commit_files` 的身份由 `/user` 决定：作者与提交者名均为 login，邮箱缺失时用 `<login>@users.noreply.local`。无法读取或解析身份时停止提交；没有覆盖参数。

缓存首次成功 fetch 后写入 `.ready`，失败初始化下次会重新 fetch，旧缓存也以一次 fetch 迁移。已就绪缓存刷新失败会报告错误，但保留已有对象。

代码搜索按全仓库总量截断，并限制 stdout、stderr 和解析缓冲；多取一条判断 `truncated`，字节预算耗尽时返回已有结果。超时和截断均回收子进程，不要求新版 Git 的 grep 参数。

`compare_commits` 默认比较两端快照；`merge_base=true` 比较共同祖先到 head，`comparison_base_sha` 返回实际基准。自己的 PR 按 `created_by` 筛选；PR 和 Issue 列表采用平台默认顺序，不保证更新时间排序。未公布分页参数的接口在响应字节预算内读取后本地分页。

Git 仅继承运行需要的环境白名单，鉴权由服务注入。`trust_env=false` 同时关闭 HTTP 和 Git 环境代理；开启后只使用受控代理和证书变量，支持 `NO_PROXY`。Git 的环境配置、其他凭据和原始 Token 不继承到子进程。

Git 错误区分 `invalid_input`、`authentication_required`、`forbidden`、`protected_branch`、`conflict` 和 `remote_rejected`。明确的 push 拒绝标记结果确定；超时或状态不明仍标记不确定。意外工具异常返回不含原异常内容的 `internal_error`，写工具保守标记不确定。

CI 另设 Linux Git 2.34.1 的本地 Git 测试任务。

## 0.3 配置与行为

新增 `tool_profile = "core" | "review" | "full"`。新安装 Core 12 个读取工具，Review 24 个读取工具和六个可选写工具；Full 有 38 个读取工具，包含兼容保留的 `search` 和新名称 `search_code`。`allowed_write_operations` 默认六项：create_branch、commit_files、create_pull_request、create_issue、add_comment_to_issue、add_review_to_pr。实际注册是工具组与权限策略的交集；空仓库名单或空操作名单均不开放写入。

默认 `protected_branches = ["main", "master"]`（精确名称）、`protect_default_branch = true`。直接分支修改会解析目标、校验策略，再由 API/Git 后端核验同一写许可；许可绑定仓库、操作、分支及 API 路径/方法。保护开启时即使显式允许，也不注册或执行 merge_pull_request/enable_auto_merge，避免 PR 目标变更的竞争。创建指向 main 的 PR 不受此限制。本地策略约束本服务，团队仍需配置平台的受保护分支和自动化规则。

旧 TOML 未声明 profile 时保留 Full，并发出迁移提示，但采用更严格的默认写操作名单。配置变更后重启。专用 `examples/acceptance.toml` 关闭本地保护以验收临时基准分支合并，仅允许 JasmineTea-test；不要用于日常课程仓库。

分页优先读取经验证的 Link，并返回 total_count、next_limit、pagination_source、pagination_verified；下一次调用使用 next_page 和 next_limit。缺失可靠头时非空页继续、空页结束，可能多请求一次。服务不直接访问 Link URL。无分页参数的接口仍做本地分页。

Git 缓存使用私有权限和版本化 `.ready` 状态。fetch 使用 prune，远端标签保存在 refs/jasminetea/origin/tags，保留本地 refs/tags 和未推送对象。旧缓存权限不安全时拒绝并指导修复，不自动 chmod 或修改 ACL；权限修复后再抓取迁移。

`git_refresh_ttl_seconds = 30` 控制远端引用读取的缓存；零表示每次刷新。比较、搜索和 Git Diff 降级可用 force_refresh；结果带实际 SHA 和缓存时间。写前始终刷新，推送使刷新状态失效。`git_fetch_timeout_seconds = 120` 独立于其他 Git 超时；客户端示例配置工具超时 300 秒。

Git Diff/提交 patch/numstat 使用有界流式读取，同时受文本、响应和工具总预算约束，因此实际文本可能低于 max_text_bytes。truncated 和 files_truncated 明确说明不完整；无法确认目标文件是否变更时不会返回虚假的 patch=null。取消、超时和截断会回收进程。

Swagger 优先动态检测；缺失时须通过基础版本探测，才启用随包的 basic-read-schema.json。兼容模式只允许经过测试的 Core GET 接口和只读 Git 操作，全部写入及其他通用 fetch 路径禁用。能力报告区分平台支持、注册状态、策略禁用和未验证接口，不能把兼容定义之外的接口认定为平台缺失。

Kimi 配置通过 `config-example --client kimi` 生成，使用维护中的 Kimi Code 用户级 mcp.json。Token 来自父进程或 TOML 同目录 .env，不把凭据值写入 JSON；不假设 Kimi 支持 Claude 的环境变量插值语法。详见安装文档。
