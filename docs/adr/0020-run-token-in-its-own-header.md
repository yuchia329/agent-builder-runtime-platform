# 0020. The run token travels in X-Run-Token; Authorization belongs to SigV4

- Status: Proposed
- Date: 2026-11-19

## Context

ADR 0007 protects the gateway's `/mcp` route on AWS with IAM authorization at API Gateway, and ADR
0003 adds the run token, which the gateway checks itself (tools, tenant, expiry). The contract put
the token in `Authorization: Bearer <token>`. SigV4 also uses `Authorization`
(`AWS4-HMAC-SHA256 Credential=...`), and a request carries one such header, so both checks could
not work together; found while adding the worker's SigV4 signer for gateway calls (M6).

Options:

- **Move the token to its own header** (`X-Run-Token`) and keep IAM on `/mcp`: a contract change
  the worker and the gateway follow.
- **Drop IAM on `/mcp`**: the token alone (KMS-signed, short-lived, scoped to the run's tools) is
  the lock. No contract change, but a leaked token works from anywhere until it expires, and
  anonymous traffic reaches the Lambda.
- **SigV4 in the query string** (presigned URL), token kept in `Authorization`: no contract change,
  but query-string IAM authorization on API Gateway HTTP APIs is not something to depend on.

## Decision

The run token goes in the `X-Run-Token` header on every `/mcp` request
(`contracts/mcp-tool-call.schema.json`). On AWS, `Authorization` carries the caller's SigV4
signature for `execute-api`, which API Gateway checks before the gateway checks the token. The
worker's signature covers `x-run-token`, so the token cannot be swapped in transit.

Changeover: unsigned workers (local) also send `Authorization: Bearer` until every gateway reads
`X-Run-Token`; the gateway accepts Bearer as a fallback meanwhile. Both copies go once both
services follow (roadmap, Cross-service changes).

## Consequences

- Two independent locks on `/mcp`: only the worker's IAM role reaches the gateway, and each run is
  limited to its own tools.
- `X-Run-Token` is not MCP's standard `Authorization` bearer scheme: a generic MCP client needs
  the extra header (the MCP SDK client takes custom headers).
- Logs and traces must treat `X-Run-Token` like `Authorization` (never recorded).
