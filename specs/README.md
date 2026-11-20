# specs

Example agent specs, run against the real model and tools. Each file follows
`contracts/agent-spec.schema.json`. The spec used by tests and `make demo` is
`contracts/examples/agent-spec.valid.yaml` (stub model, fake tools); these are the real ones.

| Spec | What it does | Tools |
| --- | --- | --- |
| `issue-triage.yaml` | Labels a new GitHub issue (`bug`, `enhancement`, ...), adds `duplicate` when an existing issue reports the same problem, and closes a high-confidence duplicate once a person approves in Slack | `github.search_issues`, `github.add_label`, `github.close_issue` (approval) |
| `needs-info.yaml` | Labels a new bug report `needs-info` when it does not say how to reproduce the problem; nothing else (the repository needs a `needs-info` label) | `github.add_label` |

Every spec here with tasks in `evals/tasks/<name>/` is scored by `make eval` (`SPEC=<name>` for
one; real model, simulated GitHub, see `evals/AGENTS.md`). Run it after changing a spec's
instructions or model, and save a new baseline (`make eval-baseline SPEC=<name>`) once the change
is accepted.

## Run issue-triage for real

Costs about $0.01 of MiniMax credit per run and writes labels to a real GitHub repo. The worker
calls the tools through the tool gateway, which acts as the GitHub App (`acts_as: team-bot`); no
GitHub token goes to the worker (ADR 0006).

1. In `.env` (repo root): the MiniMax provider lines, `GITHUB_REPOS=<owner>/<repo>` and the
   `GITHUB_APP_*` settings (see `.env.example`; the App needs Issues read and write on that one
   repo). No `GITHUB_TOKEN` is needed.
2. Start the stack with the gateway's GitHub connector. `CONNECTORS=github` on the command line
   overrides `.env` for that start; a plain `make up` afterwards goes back to the fake connector
   that `make demo` needs. The gateway logs `Credential from the environment: github team-bot`.

   ```sh
   make up && CONNECTORS=github docker compose up -d --wait tool-gateway
   ```

3. Create the agent (409 if it exists) and publish the spec. Every publish is a new version and
   becomes the one runs use:

   ```sh
   set -a && . ./.env && set +a
   curl -s -X POST localhost:8080/agents -H "X-Api-Key: $TENANT_API_KEY" \
     -H 'Content-Type: application/json' -d '{"name":"issue-triage"}'
   curl -s -X POST localhost:8080/agents/issue-triage/versions -H "X-Api-Key: $TENANT_API_KEY" \
     -H 'Content-Type: application/yaml' --data-binary @specs/issue-triage.yaml
   ```

4. Open an issue in the repo, then start a run with the same shape as GitHub's `issues.opened`
   event (only these fields are needed):

   ```sh
   curl -s -X POST localhost:8080/runs -H "X-Api-Key: $TENANT_API_KEY" \
     -H 'Content-Type: application/json' -d '{"agent":"issue-triage","input":{
       "repository":{"full_name":"<owner>/<repo>"},
       "issue":{"number":4,"title":"EUR card refunds return an error",
                "body":"Refund failed: currency not supported. USD refunds work."}}}'
   ```

5. Poll until `status` is `SUCCEEDED`, `FAILED` or `WAITING_APPROVAL`; `output` has the summary,
   `cost_usd` the spend. Then check the issue's labels on GitHub.

   ```sh
   curl -s localhost:8080/runs/<run_id> -H "X-Api-Key: $TENANT_API_KEY"
   ```

## Approve a duplicate close in Slack

When the run finds a duplicate with high confidence, it pauses in `WAITING_APPROVAL` before
`github.close_issue` and posts Approve / Reject buttons to `SLACK_APPROVAL_CHANNEL`. Approve closes
the new issue as a duplicate (linked to the original) and the run ends `SUCCEEDED`; Reject leaves
it open, keeps its labels, and the run ends `FAILED` with `approval_rejected`. Nobody deciding
within `APPROVAL_TIMEOUT_HOURS` (72) ends it with `approval_expired`.

1. In `.env`: `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_APPROVAL_CHANNEL` (Slack app setup:
   `control-plane/AGENTS.md`). Posting works without anything else.
2. Clicks need a public URL for the control plane on port 8080, through a tunnel:
   `cloudflared tunnel --url http://127.0.0.1:8080` (no account) or, where quick tunnels are
   blocked, `ngrok http 8080 --traffic-policy-file <policy>` with a policy that answers 403 to
   everything except `POST /approvals/slack` (a tunnel exposes every route, and `/internal/*`
   has no auth locally):

   ```yaml
   on_http_request:
     - expressions: ["!(req.method == 'POST' && req.url.path == '/approvals/slack')"]
       actions: [{type: custom-response, config: {status_code: 403, body: "forbidden"}}]
   ```

3. Paste `<tunnel URL>/approvals/slack` into the Slack app's Interactivity & Shortcuts → Request
   URL (new URL on every tunnel start). Messages posted before the tunnel was up still work.
4. To test, plant a duplicate: open an issue restating an existing one in other words, wait a few
   seconds for search to index it, start a run (step 4 above), click in Slack, and check the issue
   on GitHub (`gh issue view <n> --json state,stateReason`).

## Run issue-triage on AWS

The same run on the dev deployment (`infra/`): no tunnels, no local stack. Measured
(2026-11-20): a planted duplicate was labeled, approved in Slack and closed by `<app>[bot]` in
4.5 minutes, nearly 4 of them a worker starting from 0; 16 steps, $0.0076.

1. Once: the App credential into SSM (`cd tool-gateway && uv run python
   scripts/put_app_credential.py`, from `.env`; `--status` to check), and in the git-ignored
   `infra/envs/dev/terraform.tfvars`: `gateway_connectors = "github"`, `github_repos =
   "<owner>/<repo>"`; then `terraform -chdir=infra/envs/dev apply`.
2. Once: point the outside services at the API (`terraform -chdir=infra/envs/dev output -raw
   api_url`, shown as `<api>`):
   - GitHub App → General: Webhook **Active**, URL `<api>/triggers/github`, Secret = the
     `GITHUB_WEBHOOK_SECRET` that is in SSM; a Redirect URI `<api>/oauth/github/callback` next
     to the localhost one. Permissions & events → Subscribe to events: **Issues** (the list
     appears only while the webhook is active).
   - Slack app → Interactivity & Shortcuts → Request URL `<api>/approvals/slack`.
3. Publish the spec on AWS (the tenant key is in SSM, not `.env`: `infra/scripts/put_secrets.py`
   generated it): `POST <api>/agents` (409 if it exists), then `POST
   <api>/agents/issue-triage/versions` with `specs/issue-triage.yaml`, both with `X-Api-Key`.
4. Open a new issue in the sandbox that restates an open one in other words. The webhook starts
   the run; after the worker starts (about 4 minutes from 0) the Approve / Reject message
   appears in Slack.
5. Check: `GET <api>/runs/<run_id>` (the run id is in the control plane log line
   `GitHub delivery ... (github.issue.opened): started 1 run(s) [...]`, or in the delivery's
   response under App → Advanced → Recent Deliveries); the issue on GitHub; the trace in
   `aws/spans` by `trace_id`; `ModelCostUsd` in CloudWatch (`worker/AGENTS.md`).

Back to local development: point the Webhook URL and Slack's Request URL at smee and the tunnel
again (the AWS ones can be set back later; the Redirect URIs can stay).

## Gotchas

- While the AWS gateway runs `CONNECTORS=github`, do not run `infra/scripts/aws_demo.py`: it
  publishes the stub spec as `issue-triage`, so the next webhook would run the stub, and the
  stub's tool calls would reach real GitHub. To use it, set `gateway_connectors = "fake"`, apply,
  run it, then set `github` back and publish this spec again.
- Only `issues.opened` starts a run: closing and reopening an issue sends `issue.closed` and
  `issue.reopened`, which start none. Plant a new issue each time.
- The first delivery after turning the webhook on is GitHub's ping (answered `{"runs":[]}`);
  accepting changed App permissions sends `installation.new_permissions_accepted` (no run).

- `make demo` republishes `issue-triage` with the stub spec and needs the fake connector; publish
  this one again afterwards.
- The spec uses the gateway's argument names (GitHub's own: `q`, `owner`, `repo`,
  `issue_number`, `labels`, `state_reason`, `duplicate_of`). The worker's M1 tools
  (`TOOLS_PROVIDER=local`, worker on the host) take other names and do not run it.
- `docker compose logs tool-gateway | grep tool_call` shows one access line per call, with
  `started_by` (who started the run) and `identity` (`team-bot`).
- GitHub search matches exact words, not stems ("crash" misses "crashes"): the instructions tell
  the model to use short queries or `OR`. A run that misses a duplicate is usually a query issue.
- A new issue takes a few seconds to appear in GitHub search.
- `docker compose logs -f worker` shows each model call and tool call.
