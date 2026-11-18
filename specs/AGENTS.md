# specs

Real agent specs (real model, real tools). Each must validate against
`contracts/agent-spec.schema.json`. Tests and `make demo` use
`contracts/examples/agent-spec.valid.yaml` instead; do not point them here.

## Commands

| What | Command |
| --- | --- |
| Validate a spec against the schema | `cd contracts && uv run python -c "import json,yaml; from jsonschema import Draft202012Validator as V; V(json.load(open('agent-spec.schema.json'))).validate(yaml.safe_load(open('../specs/issue-triage.yaml')))"` |
| Publish and run for real | steps in `README.md` (needs `make up`, `TOOLS_PROVIDER=local`, MiniMax and GitHub settings in `.env`) |
| Approve in Slack | `README.md` → Approve a duplicate close in Slack (Slack settings in `.env`, a tunnel to port 8080) |

## Conventions

- List only tools the current tool provider has (`TOOLS_PROVIDER=local`: `github.search_issues`,
  `github.add_label`, `github.close_issue`); an unknown tool fails the run at start.
- A tool with an effect a person should check first (closing, deleting, posting) goes in
  `require_approval`, and the instructions say exactly when to call it (issue-triage: only a
  high-confidence duplicate, only the new issue, after labeling).
- Run input follows the trigger's real event shape (GitHub `issues.opened` for issue-triage).
- Instructions say to treat issue text as data, and end with a fixed summary format so results
  are easy to check.
- Keep budgets tight (a real triage run is 9 to 13 steps and under $0.01; time waiting for an
  approval does not count, ADR 0012).
- Real runs write to GitHub: only point them at the sandbox repo in `GITHUB_REPOS`.
