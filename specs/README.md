# specs

Example agent specs, run against the real model and tools. Each file follows
`contracts/agent-spec.schema.json`. The spec used by tests and `make demo` is
`contracts/examples/agent-spec.valid.yaml` (stub model, fake tools); these are the real ones.

| Spec | What it does | Tools |
| --- | --- | --- |
| `issue-triage.yaml` | Labels a new GitHub issue (`bug`, `enhancement`, ...) and adds `duplicate` when an existing issue reports the same problem | `github.search_issues`, `github.add_label` |

## Run issue-triage for real

Costs about $0.01 of MiniMax credit per run and writes labels to a real GitHub repo.

1. In `.env` (repo root): the MiniMax provider lines, `TOOLS_PROVIDER=local`, `GITHUB_TOKEN` and
   `GITHUB_REPOS=<owner>/<repo>` (see `.env.example`). The token should be fine-grained, scoped
   to that one repo, with Issues: Read and write.
2. Start the stack, or restart the worker after editing `.env`:

   ```sh
   make up                        # or: docker compose up -d --build worker
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

5. Poll until `status` is `SUCCEEDED` (or `FAILED`); `output` has the summary, `cost_usd` the
   spend. Then check the issue's labels on GitHub.

   ```sh
   curl -s localhost:8080/runs/<run_id> -H "X-Api-Key: $TENANT_API_KEY"
   ```

## Gotchas

- `make demo` republishes `issue-triage` with the stub spec; publish this one again afterwards.
- With `TOOLS_PROVIDER=local`, `make demo` fails at run start: its spec lists
  `github.close_issue` and `slack.post_message`, which the local tools do not have (M3).
- A new issue takes a few seconds to appear in GitHub search.
- `docker compose logs -f worker` shows each model call and tool call.
