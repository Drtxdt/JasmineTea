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
