"""worker/core imports no AWS or vendor SDK (AGENTS.md boundary, ADR 0004)."""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "worker" / "core"
ALLOWED_THIRD_PARTY = {"jsonschema"}  # validation library, not a vendor SDK
FORBIDDEN_PREFIXES = ("worker.adapters",)


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_core_imports_only_stdlib_worker_and_allowed_libraries():
    import sys

    offenders = []
    for path in CORE.rglob("*.py"):
        for name in _imports(path):
            top = name.split(".")[0]
            if name.startswith(FORBIDDEN_PREFIXES):
                offenders.append(f"{path.name}: {name}")
            elif top in {"worker", "__future__"} or top in sys.stdlib_module_names:
                continue
            elif top not in ALLOWED_THIRD_PARTY:
                offenders.append(f"{path.name}: {name}")
    assert offenders == []
