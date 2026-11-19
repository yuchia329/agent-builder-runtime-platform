"""Validators for the shapes in contracts/ (never copied here)."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


class Contracts:
    """Every `*.schema.json` in the folder, registered by file name so their `$ref`s resolve."""

    def __init__(self, folder: Path) -> None:
        self._schemas = {p.name: json.loads(p.read_text()) for p in folder.glob("*.schema.json")}
        if "run-token.schema.json" not in self._schemas:
            raise ValueError(f"No contracts in {folder} (set CONTRACTS_DIR)")
        self._registry = Registry().with_resources(
            (name, Resource.from_contents(schema)) for name, schema in self._schemas.items()
        )

    def validator(self, name: str) -> Draft202012Validator:
        return Draft202012Validator(self._schemas[f"{name}.schema.json"], registry=self._registry)

    def errors(self, name: str, document: object) -> list[str]:
        """Each violation as "path: message"; empty when valid."""
        return [
            f"{'.'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
            for e in self.validator(name).iter_errors(document)
        ]
