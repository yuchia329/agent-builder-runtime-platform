"""Cut a connector's openapi.yaml out of a vendor's full OpenAPI description.

Keeps only the given operations and the components they reference (transitively), so the
gateway never loads megabytes of API description. Re-run when an operation is added:

    curl -sO https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json
    uv run python scripts/cut_openapi.py api.github.com.json connectors/github/openapi.yaml \\
        --source <the URL above> search/issues-and-pull-requests issues/add-labels issues/update \\
        issues/list-labels-for-repo issues/get

Run it with `python -I` on a downloaded file kept in its own folder (untrusted input).
"""

import argparse
import datetime
import json
from typing import Any

import yaml

METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}


def refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            found.add(node["$ref"])
        for value in node.values():
            found |= refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= refs(value)
    return found


def cut(spec: dict[str, Any], operation_ids: list[str]) -> dict[str, Any]:
    wanted = set(operation_ids)
    paths: dict[str, Any] = {}
    for path, item in spec["paths"].items():
        kept = {m: op for m, op in item.items() if m in METHODS and op.get("operationId") in wanted}
        if kept:
            shared = {k: v for k, v in item.items() if k not in METHODS}
            paths[path] = {**shared, **kept}
            wanted -= {op["operationId"] for op in kept.values()}
    if wanted:
        raise SystemExit(f"Operations not found: {', '.join(sorted(wanted))}")

    components: dict[str, dict[str, Any]] = {}
    pending = refs(paths)
    seen: set[str] = set()
    while pending:
        ref = pending.pop()
        if ref in seen:
            continue
        seen.add(ref)
        _, _, kind, name = ref.split("/", 3)  # "#/components/<kind>/<name>"
        value = spec["components"][kind][name]
        components.setdefault(kind, {})[name] = value
        pending |= refs(value) - seen

    return {
        "openapi": spec["openapi"],
        "info": {"title": spec["info"]["title"], "version": spec["info"]["version"]},
        "servers": spec.get("servers", []),
        "paths": paths,
        "components": {k: dict(sorted(v.items())) for k, v in sorted(components.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source_file")
    parser.add_argument("output")
    parser.add_argument("--source", required=True, help="URL the file was downloaded from")
    parser.add_argument("operation_ids", nargs="+")
    args = parser.parse_args()
    with open(args.source_file) as f:
        spec = json.load(f)
    result = cut(spec, args.operation_ids)
    header = (
        f"# Cut by scripts/cut_openapi.py on {datetime.date.today().isoformat()} from\n"
        f"# {args.source}\n# (version {spec['info']['version']}). Do not edit by hand; re-run.\n"
    )
    with open(args.output, "w") as f:
        f.write(header)
        yaml.safe_dump(result, f, sort_keys=False, allow_unicode=True, width=100)


if __name__ == "__main__":
    main()
