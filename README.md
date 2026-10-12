# JasmineTea

让 AI 帮你使用 EduCoder 上的 Git 仓库。读代码、看提交、处理 Issue 和 PR，都可以直接在聊天里完成。

我们课程用的是 `bdgit.educoder.net`，它实际使用的是 Gitea。JasmineTea 就是给它写的一个 MCP，也可以连接其他 Gitea 平台。

## 快速开始

你需要 Python 3.11 或更新版本、Git，以及一个支持本地 MCP 的客户端。目前提供了 Codex、DeepSeek Harness、Claude Code 和 Kimi Code CLI 的配置示例。

**目前还没有发布到 PyPI，请先从源码安装。**

如果你不熟悉终端，可以把下面这句话交给你的 Agent：

> 请阅读 https://github.com/Drtxdt/JasmineTea/blob/main/docs/agent-install.md ，帮我安装 JasmineTea 并接入当前客户端，默认只读。Token 由我在本机填写，不要索取或输出它。

也可以自己来。下面以 Windows 和 Codex 为例，在 PowerShell 中运行：

```powershell
git clone https://github.com/Drtxdt/JasmineTea.git
cd JasmineTea
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

接下来，把配置放在你自己的用户目录中。第一次安装时运行：

```powershell
$teaDir = Join-Path $env:USERPROFILE ".config\jasminetea"
New-Item -ItemType Directory -Force $teaDir | Out-Null
if (-not (Test-Path "$teaDir\config.toml")) {
    Copy-Item examples\readonly.toml "$teaDir\config.toml"
}
```

登录 bdgit，在个人设置中的“应用”页面生成自己的 Token。用文本编辑器在 `$teaDir` 对应目录创建 `.env` 文件，填写：

```dotenv
BDGIT_TOKEN=你自己的Token
```

配置和 `.env` 放在同一个文件夹，之后换个项目打开 Codex 也能找到它。不要把 Token 发到聊天里，每位同学都用自己的 Token。

先检查连接，把下面的仓库名换成你有权限访问的仓库：

```powershell
.\.venv\Scripts\python.exe -m jasminetea_mcp doctor --config "$teaDir\config.toml" --repo owner/name
.\.venv\Scripts\python.exe -m jasminetea_mcp config-example --client codex --config "$teaDir\config.toml"
```

第一条命令最后显示 `"ok": true`，说明连接检查通过。第二条会输出 Codex 配置，把它合并到 `~/.codex/config.toml`，保留你原来的其他配置。不要重复添加同名的 `[mcp_servers.jasminetea]`。

重新打开 Codex 聊天，然后试着说：

> 使用 jasminetea 查看我的课程仓库，读取默认分支的 README，再看看最近几次提交。先不要修改代码。

你不需要另外开一个终端一直运行服务，客户端会启动它。Linux、DeepSeek Harness、Claude Code 和 Kimi 的步骤见[安装文档](docs/agent-install.md)。

## 能做什么

目前可以读取仓库文件和提交、搜索代码、比较分支，也可以处理 Issue、评论、标签、PR、审查与合并。

**默认只读，只显示读取工具。** 如果你确实需要 AI 修改某个仓库，在自己的 `config.toml` 中切换到 Review 档案，再添加它：

```toml
base_url = "https://bdgit.educoder.net"
token_env = "BDGIT_TOKEN"
tool_profile = "review"
write_repositories = ["你的用户名/你的仓库名"]
```

修改后重启服务。默认只开放建分支、分支提交、开 Issue/PR、评论和审查；其他写操作要单独加入 `allowed_write_operations`。默认保护 main、master 和仓库默认分支；保护开启时，合并由你在平台上操作。只有名单里的仓库允许写入，你的账号也必须有对应权限。其他 Gitea 平台可以修改 `base_url`，仓库名仍然使用 `owner/name`。

工具也可以按用途选。第一次安装默认 Core，只有 12 个读代码常用工具；Review 增加 Issue、PR 和审查；Full 保留完整工具组，仍然受写入名单限制。老配置没写 `tool_profile` 时保留 Full，但写入权限会采用新的默认限制。

Kimi 的接入示例可以这样生成，把路径换成你的配置：

```sh
python -m jasminetea_mcp config-example --client kimi --config /你的绝对路径/config.toml
```

将输出合并到 Kimi Code 的 `~/.kimi-code/mcp.json`，保留其他条目；重新开会话，用 `/mcp` 检查连接。这里指维护中的 Kimi Code CLI，旧版 kimi-cli 的目录和命令不同，别混着用。

平台没有的功能，我们也没法凭空变出来。例如当前 bdgit 没有公开 Actions、PR 草稿和审查线程解决状态的接口，会话锁定也缺少必要字段。这些操作会明确说明不支持。

提交的作者和提交者固定为你的登录账号，AI 不能指定其他人。Git 作者字段本身不能作为贡献证明，课程计分还要结合平台记录。

完整列表见[能力对照](docs/capabilities.md)。其中部分能力按 Gitea 的方式实现，不保证和 GitHub 完全相同。我们已在测试仓库跑通过提交、Issue、PR、审查和合并，但还没有把每个 API 都逐项实测，Codex 和 Claude Code 已完成真实读取及受控写入；DeepSeek Harness、Kimi 的真实模型调用仍待账号联调。具体见[验证记录](docs/validation.md)。

## 原理

原理很简单。客户端启动你本机上的 Python 程序，AI 调用工具，程序再拿你的 Token 去请求 Gitea 的 API，最后把结果返回给 AI。

一些 API 不方便完成的操作，比如代码搜索和多文件提交，会交给 Git。它在本机缓存一个 bare 仓库，不运行仓库里的代码。

所以你不需要 VPS，也不需要把服务开放到公网。但它需要联网访问 Git 平台，AI 读取到的内容也会交给你使用的模型处理。

文件更新、分支 SHA、缓存和错误处理的说明放在[技术说明](docs/technical.md)，需要时再看即可。

## 计划

- 补齐 DeepSeek Harness、Kimi Code CLI 的真实账号联调。
- 补齐更多 Gitea API 的真实测试，继续记录各个平台的差异。
- 发布到 PyPI，让安装更省事。
- 后续再考虑远程服务和 ChatGPT 网页接入。

## 贡献

如果你发现了问题，或者你的学校也用了 Gitea，欢迎提出 Issue。记得说明平台和报错信息，不要附上 Token。

想改代码的话，可以 Fork 后提交 Pull Request。开发环境和检查命令见[技术说明](docs/technical.md#开发与打包)。本项目使用 [MIT](LICENSE) 许可证。

## 一点碎碎念

做这个的起因是课程项目。代码放在老师的平台上，和 AI 讨论问题时还要来回复制文件，组员的提交、Issue 和 PR 也不太方便一起看。

既然 bdgit 用的是 Gitea，我们就试着把它接到平时用的 AI 工具里，也希望能帮到使用其他 Gitea 平台的同学。

如果这个项目帮到了你，欢迎点亮一个 Star，也欢迎告诉我们哪里还不好用。
