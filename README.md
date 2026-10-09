# JasmineTea MCP

Python 3.11+ 的 EduCoder / Gitea 协作 MCP，提供标准 stdio 服务，面向 Codex、DeepSeek Harness 和 Claude Code。

默认连接 `https://bdgit.educoder.net`，可以通过 TOML 改为其他 Gitea 实例。仓库使用 `owner/name` 标识，不绑定课程或测试仓库。

## 安装与启动

开发安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:BDGIT_TOKEN = "你的个人Token"
.\.venv\Scripts\jasminetea-mcp.exe doctor --repo hndx202426010302/JasmineTea-test
.\.venv\Scripts\jasminetea-mcp.exe serve
```

也可在启动目录或选定配置的目录建立 `.env`，仅保存 `BDGIT_TOKEN=...`。非空环境变量优先，客户端转发空变量时可以使用本地文件。只提取当前配置指定的 Token 变量；示例 Token 不会被接受。不要把 `.env` 提交或放入客户端配置。文件已被忽略，源码与发布包不包含 Token。

`examples/readonly.toml` 默认只读。`examples/test-repository.toml` 仅开启 `hndx202426010302/JasmineTea-test` 的写入：

```toml
base_url = "https://bdgit.educoder.net"
token_env = "BDGIT_TOKEN"
write_repositories = ["hndx202426010302/JasmineTea-test"]
```

Token 使用 `Authorization: token ...`。每位成员使用自己的凭据；账户仍需拥有平台对应权限。API 与 Git 的所有写入口都会核对可写名单，工具调用无法修改该名单。

## 能力

- 用户、仓库、组织和团队信息；仓库权限、目录、文本文件、Blob、提交、分支和状态。
- Issue、评论、标签、负责人、Reaction；PR、差异、变更文件、审查、审查请求、合并和原生自动合并。
- Git 补充代码搜索、提交搜索、提交比较、Git 对象创建、多文件原子提交及带 SHA lease 的分支更新。
- `get_capabilities` 对照当前 GitHub 连接器的 89 项能力，并核对实例 Swagger 的端点和字段。

工具结果包含分页或截断提示。`read_file` 支持行范围、拒绝二进制；Diff 文本默认最多 512 KiB。HTTP 响应默认最多 16 MiB，发给模型的单个工具结果默认最多 1 MiB。目录也在服务端切页。分页默认 50 条，上限 100 条，使用 `next_page` 继续；超限时缩小页、路径或行范围。

完整对照见 [能力矩阵](docs/capabilities.md)。当前 bdgit 没有公开 Actions、GitHub App 安装、原生草稿生命周期和 resolved-review-thread API。会话锁定缺少 `is_locked` 写字段，也会返回明确的 `unsupported`。部分工具按 Gitea 语义实现，差异在工具说明和矩阵中列出。

“available”表示 API 定义或本地实现具备能力，不代表你的 Token 已通过真实调用验证。`doctor` 返回鉴权、仓库权限、Git 连通性及能力报告。

## Git 后端

需要系统 Git。服务使用用户缓存目录中的隔离 bare 仓库，不 checkout、不运行仓库代码或 hooks，也不提供任意命令工具。可用 `cache_dir` 更改缓存位置。多个进程通过文件锁串行访问同一缓存。

Git 凭据通过进程环境传入，不写入远端 URL、命令参数或仓库配置。缓存按平台、仓库与凭据隔离。Git 的错误输出不会直接交给模型。

`create_blob` / `create_tree` / `create_commit` 创建的是本地对象，可用返回 SHA 继续构建；`update_ref` 才发布可达提交。这与 GitHub 在服务器存储对象不同。`update_ref` 和 `commit_files` 必须提供当前分支的完整 `expected_sha`，使用原子 lease，并拒绝非 fast-forward 更新。

文件 API 的 `update_file` / `delete_file` 要求当前 **Blob SHA**，不是分支 SHA。多文件提交使用分支 SHA，可避免单文件 API 之间的中间状态。

只读请求可有限重试；写请求不自动重试。`result_uncertain=true` 表示远端结果不确定，应先查询分支/对象状态再决定下一步。

## 客户端

生成与你当前 Python 安装对应的配置；输出不含实际 Token：

```powershell
jasminetea-mcp config-example --client codex --config examples/readonly.toml
jasminetea-mcp config-example --client deepseek --config examples/readonly.toml
jasminetea-mcp config-example --client claude --config examples/readonly.toml
```

### Codex

将生成的 TOML 合并到现有 MCP 配置，保留其他配置。`env_vars = ["BDGIT_TOKEN"]` 转发启动 Codex 时的本地变量。也可注册当前 Python：

```powershell
codex mcp add jasminetea -- python -m jasminetea_mcp serve --config E:\my_code\JasmineTea\examples\readonly.toml
```

之后在对应服务器配置中添加 `env_vars`。Python 必须是已经安装本包的解释器，推荐使用生成示例中的绝对路径。

### DeepSeek Harness

将生成的 Cordis overlay 保存后使用 `dsh web --no-open --patch <配置路径>`，或合并到已有 patch。使用 `@deepseek-ai/dsh-mcp-client`，显式从 `process.env.BDGIT_TOKEN` 转发凭据；凭据值不写入 YAML。等待工具发现完成再调用。

### Claude Code

将生成的 JSON 合并到 MCP 配置，`${BDGIT_TOKEN:-}` 引用启动环境的变量，缺失时让服务读取本地 `.env`。也可使用：

```powershell
claude mcp add --transport stdio jasminetea -- python -m jasminetea_mcp serve --config E:\my_code\JasmineTea\examples\readonly.toml
```

同样使用安装本包的 Python；通过继承的环境或本地 `.env` 提供凭据。

## 测试与打包

```powershell
python -m pytest
python -m ruff check src tests
python -m build --no-isolation
python -m twine check dist/*
```

自动测试包括 API 合同与分页、全部写入口的权限检查、网络错误及不确定结果、真实本地 bare Git 的并发 lease / 多文件提交 / 中文路径 / 二进制 / Diff，以及 MCP 新旧协议调用。Windows 与 Linux 的 CI 配置位于 `.github/workflows/tests.yml`。

真实平台验收使用 `scripts/live_acceptance.py`，只在专用测试仓库创建 `jasminetea-test/` 前缀的分支和带标识的 Issue/PR，并写入清理记录。需要个人 Token，真实验证情况见 [验证记录](docs/validation.md)。本地 mock、平台实时联调和三个客户端的模型调用分别记录，不能互相替代。

包名 `jasminetea-mcp` 在规划时尚未占用，正式发布前重新检查。构建 wheel/sdist 和本地安装验证后，再通过 TestPyPI 验证，最终上传 PyPI；当前实现不自动上传。

## 接口依据

- [bdgit 实例 Swagger](https://bdgit.educoder.net/swagger.v1.json)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp#stdio-servers)
- [DeepSeek MCP 接入](https://deepseek-harness.github.io/deepseek-harness/en/guide/mcp-memory)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
