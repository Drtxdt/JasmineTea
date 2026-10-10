# 让 Agent 帮你安装 JasmineTea

如果你是第一次配置 MCP，可以把这份文档的链接发给你使用的 Agent，再告诉它你使用的客户端和想访问的仓库。大部分终端操作可以交给它，Token 需要你自己在本机填写。

JasmineTea 目前没有发布到 PyPI，下文从源码安装。它在你的本机运行，客户端会自动启动服务，不需要公网地址。

## 给 Agent 的任务

> 请按照这份文档安装 JasmineTea 并接入我的客户端，默认只读。先检查我的环境，保留已有配置，不要索取或输出 Token。需要我提供凭据时，告诉我应该在本机哪个文件中填写。安装后检查连接、工具发现和一次实际仓库读取，分别报告结果。

执行时遵循这些约定：

- 检测已有 Python、Git、客户端和安装目录，优先复用合适的环境，不覆盖已有目录或虚拟环境。
- 仅在必要时询问目标客户端、安装位置、平台地址和 `owner/name`。默认平台是 bdgit；不知道仓库名时询问用户，不把作者的测试仓库当成所有人都能访问的仓库。
- 配置默认 `write_repositories = []`。安装验收不创建 Issue、PR、分支，不修改或删除远端文件。
- Token 由用户手动填写。不要读取、打印或复制已有 `.env`，不要通过聊天索取 Token，也不要将值写入客户端配置或命令参数。
- 合并客户端配置前备份；只增改 JasmineTea 的条目，保留其他设置。同名服务器已经存在时核对配置，不重复注册。
- 每一步核对退出码。终端命令成功、协议连接成功和实际仓库读取成功是三个不同结果；未完成的检查如实说明。

## 1. 检查环境

Windows PowerShell：

```powershell
python --version
git --version
Get-Command codex, dsh, claude -ErrorAction SilentlyContinue
```

Linux：

```sh
python3 --version
git --version
command -v codex
command -v dsh
command -v claude
```

Python 需要 3.11 或更新版本，Git 用于代码搜索和 Git 操作。只需检测并安装用户选中的客户端，不要求三者同时存在。

没有 Python/Git 时，Windows 可从 [Python](https://www.python.org/downloads/windows/) 和 [Git](https://git-scm.com/downloads/win) 官网安装，安装后重新打开终端。Linux 使用发行版包管理器；例如 Ubuntu/Debian 可用 `sudo apt install python3 python3-venv git`，但仍需核对 Python 版本，不要用旧版本替换系统 Python。

缺少目标客户端时，先依据它的官方安装说明准备客户端并完成登录，再继续。不要为了安装 JasmineTea 顺便安装其他客户端。

## 2. 安装源码

选一个用户可写的目录。若已经下载过项目，检查远端与目录内容后复用，不覆盖已有文件。下面命令用于首次安装。

Windows：

```powershell
git clone https://github.com/Drtxdt/JasmineTea.git
cd JasmineTea
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$teaPython = (Resolve-Path .venv\Scripts\python.exe).Path
& $teaPython -m jasminetea_mcp --version
```

Linux：

```sh
git clone https://github.com/Drtxdt/JasmineTea.git
cd JasmineTea
python3 -m venv .venv
.venv/bin/python -m pip install .
tea_python="$(pwd)/.venv/bin/python"
"$tea_python" -m jasminetea_mcp --version
```

成功表现：安装命令退出码为 0，版本检查显示 `0.2.0`。不需要激活虚拟环境，后续一直使用它的绝对解释器路径。

本地已有 wheel 时也可以在同一个虚拟环境中执行 `python -m pip install <wheel的绝对路径>`。不要尝试从 PyPI 安装尚未发布的 `jasminetea-mcp`。

## 3. 创建私人配置

将配置和 `.env` 放在同一目录，而不是依赖客户端恰好从源码目录启动。已经有配置时先读取非敏感的 TOML 并保留用户选择，不覆盖它。

Windows 首次配置：

```powershell
$teaDir = Join-Path $env:USERPROFILE ".config\jasminetea"
New-Item -ItemType Directory -Force $teaDir | Out-Null
$teaConfig = Join-Path $teaDir "config.toml"
if (-not (Test-Path $teaConfig)) {
    Copy-Item examples\readonly.toml $teaConfig
}
```

Linux 首次配置：

```sh
tea_dir="${XDG_CONFIG_HOME:-$HOME/.config}/jasminetea"
mkdir -p "$tea_dir"
chmod 700 "$tea_dir"
tea_config="$tea_dir/config.toml"
if [ ! -f "$tea_config" ]; then
    cp examples/readonly.toml "$tea_config"
fi
```

默认内容是：

```toml
base_url = "https://bdgit.educoder.net"
token_env = "BDGIT_TOKEN"
write_repositories = []
```

请用户登录 bdgit，在个人设置中的“应用”页面生成自己的 Token，然后用文本编辑器在这个目录创建 `.env`：

```dotenv
BDGIT_TOKEN=你的个人Token
```

Agent 此时只告知实际目录并等待用户确认已填写，不读取文件内容。Linux 用户保存后可执行 `chmod 600 "$tea_dir/.env"`。Windows 用户留在自己的用户目录，不放进共享目录。

非空环境变量会优先于 `.env`。如果用户之前设置了旧 Token，告知这一规则，请用户自行更新或清除旧变量；不要显示变量值。自定义 `token_env` 时，用户应填写相同变量名。

## 4. 检查连接

将下方 `owner/name` 替换成用户实际有权访问的仓库。

Windows：

```powershell
& $teaPython -m jasminetea_mcp doctor --config $teaConfig --repo owner/name
```

Linux：

```sh
"$tea_python" -m jasminetea_mcp doctor --config "$tea_config" --repo owner/name
```

成功表现：退出码为 0，最终 `ok` 为 `true`；鉴权、仓库、Git 连通性检查均成功，`write_enabled` 为 `false`。没有提交的空仓库无法解析默认分支，不能为了通过检查擅自创建提交；选择有内容的仓库或报告这一限制。

`doctor` 不负责安装客户端，也不代表客户端已经连接。下一步还要验证 MCP。

## 5. 接入客户端

### Codex

生成配置：

```powershell
& $teaPython -m jasminetea_mcp config-example --client codex --config $teaConfig
```

Linux 对应命令：

```sh
"$tea_python" -m jasminetea_mcp config-example --client codex --config "$tea_config"
```

把输出合并到 `~/.codex/config.toml`；Windows 默认是 `%USERPROFILE%\.codex\config.toml`，使用自定义 `CODEX_HOME` 时以实际目录为准。输出包含 `[mcp_servers.jasminetea]`、绝对 Python 路径和绝对配置路径，以及 `env_vars = ["BDGIT_TOKEN"]`，不含 Token 的值。

`env_vars` 负责转发启动环境的变量；没有非空变量时，服务会读取 TOML 同目录的 `.env`。无需把 Token 写入 Codex 配置。

运行 `codex mcp list` 确认条目，再重新打开聊天。要求客户端发现 `get_capabilities`、`get_repo`、`read_file` 等工具，并完成后面的读取验证。仅显示服务器条目不能算工具调用通过。

如果复用的是源码根目录 `.env` 和 `examples/readonly.toml`，两者不在同一目录，需要在 `[mcp_servers.jasminetea]` 增加 `cwd = "源码根目录的绝对路径"`。新安装优先采用上面的私人配置目录。

[Codex 官方配置说明](https://learn.chatgpt.com/docs/extend/mcp)

### DeepSeek Harness

Windows 生成 Cordis overlay 文件：

```powershell
& $teaPython -m jasminetea_mcp config-example --client deepseek --config $teaConfig |
    Set-Content -LiteralPath "$teaDir\jasminetea.cordis.yml" -Encoding utf8
dsh web --no-open --patch "$teaDir\jasminetea.cordis.yml"
```

Linux：

```sh
"$tea_python" -m jasminetea_mcp config-example --client deepseek --config "$tea_config" > "$tea_dir/jasminetea.cordis.yml"
dsh web --no-open --patch "$tea_dir/jasminetea.cordis.yml"
```

生成文件前确认同名文件不存在；已有文件时合并 JasmineTea 条目，不覆盖其他 patch。已经使用 Cordis patch 的用户，把生成的 `insert` 项合并进已有 patch，保留其他配置。

配置使用 `@deepseek-ai/dsh-mcp-client`。DSH 会过滤环境中的常见凭据变量，因此生成配置显式使用 `env` 引用 `process.env.BDGIT_TOKEN || ''`。为空时由服务读取配置目录 `.env`，不要把实际 Token 写入 YAML。

等待异步工具发现完成，确认出现 `mcp__jasminetea__get_capabilities` 等工具，再做读取验证。DSH 自己的模型配置和登录问题应单独处理。

[DeepSeek Harness 官方接入说明](https://deepseek-harness.github.io/deepseek-harness/en/guide/mcp-memory)

### Claude Code

先确认没有同名服务器，然后注册到用户范围；已有条目时只更新 JasmineTea 的路径，不移除其他服务器。

Windows：

```powershell
claude mcp add --transport stdio --scope user jasminetea -- $teaPython -m jasminetea_mcp serve --config $teaConfig
claude mcp list
```

Linux：

```sh
claude mcp add --transport stdio --scope user jasminetea -- "$tea_python" -m jasminetea_mcp serve --config "$tea_config"
claude mcp list
```

服务从配置同目录读取 `.env`，不需要在注册命令中传 Token。也可以用 `config-example --client claude --config <绝对配置路径>` 生成 JSON，合并到现有 MCP 配置；其中 `${BDGIT_TOKEN:-}` 是变量引用。

打开 Claude Code，通过 `/mcp` 检查连接状态，然后做读取验证。用户范围适用于不同项目；只想对一个项目启用时，可以改用 `--scope project`，但不要覆盖项目已有 `.mcp.json`。

[Claude Code 官方配置说明](https://code.claude.com/docs/en/mcp)

## 6. 验证一次实际读取

在用户选定的客户端发出：

> 使用 jasminetea 的 get_capabilities 查询平台能力，再用 get_repo 查看 owner/name 的默认分支，最后用 read_file 读取默认分支上的 README。不要修改仓库。

把仓库和文件路径换成真实存在的值。默认分支可能不是 `main`，文件也可能叫 `README.rst`，先查看结果，不要猜。成功表现是工具实际执行、返回真实的仓库元数据和文件内容，而不是模型只解释它打算怎么做。

完成后报告安装位置、解释器路径、配置路径、客户端版本，以及版本检查、doctor、工具发现、实际读取的各自结果。不要记录 Token。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| `No module named jasminetea_mcp` | 客户端用了错误的 Python。改成安装本包的虚拟环境绝对路径。 |
| 找不到 Token / `authentication_required` | 核对配置目录是否有 `.env`、变量名是否一致、是否误用示例值；让用户检查，不打印文件。 |
| `unauthenticated` / 401 | Token 缺失或失效，用户在本机更新。 |
| `forbidden` / 403 | 检查账号权限、Token 权限和平台是否限制 API。 |
| `not_found_or_hidden` / 404 | 检查平台、仓库、路径和 ref；私有仓库无权限也可能返回 404。 |
| `write_disabled` | 默认只读的正常结果。只有用户明确要写入指定仓库时才修改名单。 |
| Git 检查失败 | 检查 Git 是否安装、网络是否能连接平台、仓库是否已有提交以及默认分支是否存在。 |
| `unsupported` | 查看能力表，平台缺少对应接口或字段，不靠重试解决。 |
| 超时、连接失败或限流 | 根据 doctor 的错误检查网络、代理和平台状态。写入结果不确定时先核实，不重复提交。 |
| 手工运行 `serve` 后一直没输出 | stdio 服务等待客户端发消息，这是正常的。用客户端启动，不把它当网页服务。 |
| 配置有条目但没有工具 | 重启客户端，检查服务器启动错误；DSH 工具发现是异步的，需要等待。 |

更多细节见[技术说明](technical.md)，已完成和未完成的验证见[验证记录](validation.md)。

## 从 0.1 升级

重新安装源码，重启客户端。默认只读现在只显示 37 个工具，可写名单非空时显示 68 个工具。`get_capabilities.registered_tools` 给出实际注册列表。`create_commit` 和 `commit_files` 不再接受作者参数，旧配置不需要添加身份。

代理默认关闭。如果此前 Git 依赖环境代理，在 TOML 中设置 `trust_env = true`，重新运行 `config-example`，把输出合并到客户端配置。这会显式转发 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY、NO_PROXY 和 SSL_CERT_FILE/SSL_CERT_DIR 的变量引用；不要把代理密码写进配置。
