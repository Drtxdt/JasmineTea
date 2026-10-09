import json
from importlib.resources import files

from .api import GiteaClient


def reference_matrix() -> list[dict]:
    return json.loads(files("jasminetea_mcp").joinpath("parity.json").read_text(encoding="utf-8"))


def capability_report(schema: dict | None, methods: dict, git_available: bool) -> list[dict]:
    result = []
    for original in reference_matrix():
        row = dict(original)
        name = row["tool"]
        if row["backend"] == "unsupported":
            row["status"] = "unsupported"
        elif name not in methods:
            row["status"] = "implementation_missing"
        else:
            meta = methods[name].tea_meta
            if row["backend"] == "git" and not git_available:
                row["status"] = "git_unavailable"
            elif meta["path"] is None:
                row["status"] = "available"
            elif schema is None:
                row["status"] = "unverified"
            else:
                operation = schema.get("paths", {}).get(meta["path"], {}).get(meta["method"])
                available = operation is not None
                if available and meta["fields"]:
                    props = GiteaClient.body_properties(schema, operation)
                    available = all(field in props for field in meta["fields"])
                row["status"] = "available" if available else "unsupported"
            row["write"] = meta["write"]
        row["verification"] = "schema_only_not_authenticated" if schema is not None else "not_live_verified"
        result.append(row)
    return result
