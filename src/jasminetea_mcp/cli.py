import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .config import load_settings
from .errors import TeaError
from .service import TeaService


def client_example(client: str, config: str | None = None) -> str:
    args = ["-m", "jasminetea_mcp", "serve"]
    token_env = "BDGIT_TOKEN"
    if config:
        config = str(Path(config).resolve())
        args += ["--config", config]
        if Path(config).is_file():
            import tomllib

            from .config import Settings

            with Path(config).open("rb") as stream:
                token_env = Settings(**tomllib.load(stream)).token_env
    if client == "codex":
        return (
            "[mcp_servers.jasminetea]\ncommand = "
            + json.dumps(sys.executable)
            + "\nargs = "
            + json.dumps(args)
            + "\nenv_vars = "
            + json.dumps([token_env])
            + "\n"
        )
    if client == "claude":
        return (
            json.dumps(
                {
                    "mcpServers": {
                        "jasminetea": {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": args,
                            "env": {token_env: "${" + token_env + ":-}"},
                        }
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    return (
        "- insert:\n    - id: jasminetea-mcp\n      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n        serverName: jasminetea\n        transport: stdio\n"
        "        command: "
        + json.dumps(sys.executable)
        + "\n        args: "
        + json.dumps(args)
        + "\n        env:\n          "
        + token_env
        + ": !!js process.env."
        + token_env
        + " || ''\n"
    )


async def diagnose(service: TeaService, repo: str | None) -> dict:
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": service.settings.base_url,
        "git_available": bool(service.git.git),
        "checks": {},
    }
    try:
        report["capabilities"] = await service.get_capabilities()
        try:
            user = await service.get_profile()
            report["checks"]["authentication"] = {"ok": True, "login": user.get("login")}
        except TeaError as error:
            report["checks"]["authentication"] = {"ok": False, **error.as_dict()}
        if repo:
            try:
                data = (await service.get_repo(repo))["data"]
                report["checks"]["repository"] = {
                    "ok": True,
                    "permissions": data.get("permissions"),
                    "default_branch": data.get("default_branch"),
                    "write_enabled": repo in service.settings.write_repositories,
                }
                ref = data.get("default_branch")
                if ref and service.git.git:
                    try:
                        sha = await service._git(repo, "resolve", ref)
                        report["checks"]["git_access"] = {"ok": True, "sha": sha}
                    except TeaError as error:
                        report["checks"]["git_access"] = {"ok": False, **error.as_dict()}
            except TeaError as error:
                report["checks"]["repository"] = {"ok": False, **error.as_dict()}
        report["ok"] = (
            all(c["ok"] for c in report["checks"].values()) and report["capabilities"]["schema_verified"]
        )
        return report
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="JasmineTea Gitea collaboration MCP")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "doctor"):
        item = sub.add_parser(name)
        item.add_argument("--config")
        if name == "doctor":
            item.add_argument("--repo")
    example = sub.add_parser("config-example")
    example.add_argument("--client", required=True, choices=["codex", "deepseek", "claude"])
    example.add_argument("--config")
    args = parser.parse_args()
    try:
        if args.command == "config-example":
            print(client_example(args.client, args.config), end="")
            return
        settings = load_settings(args.config)
        service = TeaService(settings)
        if args.command == "serve":
            from .server import create_server

            settings.token()
            create_server(service).run(transport="stdio")
        else:
            report = asyncio.run(diagnose(service, args.repo))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not report["ok"]:
                raise SystemExit(1)
    except TeaError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValidationError, ValueError):
        # Configuration exceptions can include supplied values; do not print them.
        print("Invalid configuration or unreadable configuration file.", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
