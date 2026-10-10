"""Generate documentation from the packaged 89-tool reference and recorded instance schema."""

import json
from pathlib import Path

from jasminetea_mcp.config import Settings
from jasminetea_mcp.parity import capability_report
from jasminetea_mcp.service import TeaService

root = Path(__file__).resolve().parents[1]
schema = json.loads((root / "tests/fixtures/bdgit-swagger.json").read_text(encoding="utf-8"))
service = TeaService(Settings())
rows = capability_report(schema, service.methods(), True)
labels = {"api": "API", "git": "Git", "mapped": "等价映射", "unsupported": "平台缺失"}
lines = [
    "# 89 项能力对照",
    "",
    "基线：2026-10-09 当前连接器工具清单及 bdgit 的公开 Swagger 快照。",
    "",
    "表中的支持状态只表示接口/实现存在，不能替代带 Token 的平台联调。源数据随 Python 包分发。",
    "",
    "验证：API 对照由 `tests/test_service.py` 检查路径、query 名称/类型/枚举和请求 JSON Schema；",
    "Git 行为由 `tests/test_git.py` 的真实 bare 仓库测试验证；MCP 由 `tests/test_mcp.py`、",
    "`tests/test_stdio.py` 覆盖。真实平台和客户端运行情况单列在 `validation.md`。",
    "",
    "| 原工具 | 实现方式 | JasmineTea 工具 | 实例支持 | 差异 |",
    "|---|---|---|---|---|",
]
for row in rows:
    lines.append(
        f"| `{row['reference']}` | {labels[row['backend']]} | "
        f"{('`' + row['tool'] + '`') if row['tool'] else '—'} | {row['status']} | {row['notes']} |"
    )
target = root / "docs/capabilities.md"
target.parent.mkdir(exist_ok=True)
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Generated {len(rows)} rows.")
