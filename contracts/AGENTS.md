# contracts

Shared shapes between services. Language-neutral: JSON Schema (draft 2020-12) and, later, OpenAPI.

## Commands

- Test: `uv run pytest` (from `contracts/`). Checks each schema is valid, valid examples pass,
  invalid examples fail.

- Tests hang at 0% CPU: iCloud evicted `.venv` files (repo under `~/Desktop`). Check
  `find .venv -flags +dataless | wc -l`; if not 0, `rm -rf .venv && uv sync`.

- Optional extra lint for OpenAPI files: `npx -y @redocly/cli@1.34.5 lint internal-api.openapi.yaml`
  (expect one `info-license` warning until the repo's license is decided).

## Conventions

- HTTP APIs are OpenAPI 3.1 (`<name>.openapi.yaml`); they `$ref` sibling schema files instead of
  copying them. Tests validate the document and check examples against its component schemas.
- JSON field names are snake_case.

- One schema per file: `<name>.schema.json`.
- Every schema has exactly one `examples/<name>.valid.*` and one `examples/<name>.invalid.*`
  (YAML or JSON); the test finds them by name.
- `additionalProperties: false` on objects, so unknown fields are rejected.
- Rules JSON Schema cannot express go in the schema's `description`, and the consuming service
  enforces them in code.
- A breaking change to a shape (bump `schema_version` where present) must be listed under
  "Cross-service changes" in `docs/roadmap.md` with the services that must follow.
