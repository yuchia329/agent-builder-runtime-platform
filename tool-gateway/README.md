# tool-gateway

The only service that holds credentials for outside APIs. Workers call it over MCP with a signed
run token; it lists and runs only the tools that run is allowed, logs every call, and (from M4)
generates tools from each connector's OpenAPI file and allowlist. See `AGENTS.md` for commands
and `docs/architecture.md` → Tool gateway for the design.
