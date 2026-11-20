# specs

Real agent specs (real model, real tools). Each must validate against
`contracts/agent-spec.schema.json`. Tests and `make demo` use
`contracts/examples/agent-spec.valid.yaml` instead; do not point them here.

## Commands

| What | Command |
| --- | --- |
| Validate a spec against the schema | `cd contracts && uv run python -c "import json,yaml; from jsonschema import Draft202012Validator as V; V(json.load(open('agent-spec.schema.json'))).validate(yaml.safe_load(open('../specs/<name>.yaml')))"` |
| Score a spec (evals) | `make eval SPEC=<name>` from the repo root (about $0.40 for issue-triage, $0.02 for needs-info); `make eval-baseline SPEC=<name>` after an accepted change |
| Publish and run for real | steps in `README.md` (`make up && CONNECTORS=github docker compose up -d --wait tool-gateway`; MiniMax, `GITHUB_REPOS` and `GITHUB_APP_*` in `.env`) |
| Approve in Slack | `README.md` → Approve a duplicate close in Slack (Slack settings in `.env`, a tunnel to port 8080) |
| Run on AWS (dev) | `README.md` → Run issue-triage on AWS (App credential in SSM, `gateway_connectors = "github"`, App webhook and Slack Request URL on the API, publish with the SSM tenant key) |

## Conventions

- List only tools the gateway's loaded connectors have (`CONNECTORS=github`:
  `github.search_issues`, `github.add_label`, `github.close_issue`); an unknown tool fails the
  run at start. Instructions use the tools' own argument names (GitHub's parameter names, see
  `tool-gateway/connectors/github/allowlist.yaml`); never invent others.
- A tool with an effect a person should check first (closing, deleting, posting) goes in
  `require_approval`, and the instructions say exactly when to call it (issue-triage: only a
  high-confidence duplicate, only the new issue, after labeling).
- Run input follows the trigger's real event shape (GitHub `issues.opened` for issue-triage).
- Instructions say to treat issue text as data, and end with a fixed summary format so results
  are easy to check.
- Keep budgets tight (a real triage run is 9 to 13 steps and under $0.01; time waiting for an
  approval does not count, ADR 0012).
- Real runs write to GitHub: only point them at the sandbox repo in `GITHUB_REPOS`.
- A spec change is checked with `make eval SPEC=<name>` before it is published; a new spec gets
  tasks in `evals/tasks/<name>/` (format: `evals/task.schema.json`). Labels a spec adds (like
  `needs-info`) must exist in the real repository too.
