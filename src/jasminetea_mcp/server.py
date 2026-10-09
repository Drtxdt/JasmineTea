import json
from contextlib import asynccontextmanager
from functools import wraps

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import __version__
from .errors import TeaError
from .service import TeaService


def create_server(service: TeaService) -> MCPServer:
    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await service.close()

    server = MCPServer("JasmineTea", version=__version__, log_level="WARNING", lifespan=lifespan)
    for name, method in service.methods().items():
        meta = method.tea_meta

        def wrap(function, metadata):
            @wraps(function)
            async def call(*args, **kwargs):
                try:
                    if metadata["path"]:
                        # Discovery lists stable tools; live capability checks reject absent API operations.
                        if metadata["write"]:
                            repo = kwargs.get("repo")
                            if repo is not None:
                                service.settings.authorize_write(repo)
                        await service.api.require(
                            metadata["path"], metadata["method"], fields=list(metadata["fields"])
                        )
                    result = await function(*args, **kwargs)
                    serialized = json.dumps(result, ensure_ascii=False)
                    if len(serialized.encode("utf-8")) > service.settings.max_tool_bytes:
                        raise TeaError(
                            "response_too_large",
                            "Tool output is too large; use pagination, a file path, or a line range.",
                            uncertain=metadata["write"],
                        )
                    return CallToolResult(
                        content=[TextContent(type="text", text=serialized)],
                        structured_content=result,
                    )
                except TeaError as error:
                    return CallToolResult(
                        is_error=True,
                        content=[
                            TextContent(type="text", text=json.dumps(error.as_dict(), ensure_ascii=False))
                        ],
                    )

            return call

        server.tool(
            name=name,
            annotations=ToolAnnotations(
                read_only_hint=not meta["write"],
                destructive_hint=meta["destructive"],
                idempotent_hint=not meta["write"],
                open_world_hint=True,
            ),
        )(wrap(method, meta))
    return server
