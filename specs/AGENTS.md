# specs

Real agent specs (real model, real tools). Each must validate against
`contracts/agent-spec.schema.json`. Tests and `make demo` use
`contracts/examples/agent-spec.valid.yaml` instead; do not point them here.

## Commands

| What | Command |
| --- | --- |
| Validate a spec against the schema | `cd contracts && uv run python -c "import json,yaml; from jsonschema import Draft202012Validator as V; V(json.load(open('agent-spec.schema.json'))).validate(yaml.safe_load(open('../specs/issue-triage.yaml')))"` |
| Publish and run for real | steps in `README.md` (needs `make up`, `TOOLS_PROVIDER=local`, MiniMax and GitHub settings in `.env`) |

## Conventions

- List only tools the current tool provider has (`TOOLS_PROVIDER=local`: `github.search_issues`,
  `github.add_label`); an unknown tool fails the run at start.
- Run input follows the trigger's real event shape (GitHub `issues.opened` for issue-triage).
- Instructions say to treat issue text as data, and end with a fixed summary format so results
  are easy to check.
- Keep budgets tight (a real triage run is about 10 steps and under $0.01).
- Real runs write to GitHub: only point them at the sandbox repo in `GITHUB_REPOS`.
