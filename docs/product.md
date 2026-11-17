# Product

What the platform does and how people use it. For how it is built, see
[architecture.md](architecture.md).

## Problem

Teams want small AI agents that react to events in their tools: triage a new issue, prepare for a
meeting, summarize a thread. Building each one from scratch means solving the same problems every
time: running the agent loop, retrying after crashes without repeating actions, keeping
credentials safe, asking a human before risky actions, controlling cost, and knowing whether a
prompt change made the agent worse.

This platform solves those once. A team describes an agent in a spec file; the platform runs it.

## Users

The platform is run by an internal platform team for the engineering teams in one company. Each
team is a tenant.

| Role            | Does                                                                        |
| --------------- | --------------------------------------------------------------------------- |
| Agent author    | Writes and publishes agent specs for their team; reads run results          |
| Approver        | Approves or rejects risky actions from Slack                                |
| Platform team   | Operates the platform, adds connectors, watches cost and reliability       |

## Concepts

| Term           | Meaning                                                                                       |
| -------------- | --------------------------------------------------------------------------------------------- |
| Tenant         | A team. Every agent, run and credential belongs to one tenant.                                |
| Agent          | A named automation owned by a tenant, for example `issue-triage`.                              |
| Spec           | The YAML file that defines an agent: model, instructions, trigger, tools, approvals, budget.  |
| Version        | An immutable published spec. Publishing a change creates a new version.                       |
| Active version | The version new runs use. Rollback means pointing it back at an older version.               |
| Trigger        | The event that starts a run, for example `github.issue.opened`.                               |
| Run            | One execution of an agent version for one trigger event.                                      |
| Step           | One model turn or one tool call inside a run.                                                 |
| Tool           | One allowed API operation the agent can call, for example `github.add_label`.                 |
| Connector      | The set of tools for one outside service (GitHub, Slack, Notion, Google Calendar).            |
| `acts_as`      | Whose credentials a run uses: the agent owner, or a team bot.                                 |
| Approval       | A human decision required before a tool listed in `require_approval` runs.                    |
| Budget         | Per-run limits on steps, dollars and seconds.                                                 |

## Agent spec

What an agent author writes:

```yaml
name: issue-triage
model: anthropic.claude-haiku-5-5 # any Bedrock model id
instructions: Triage new issues for the payments repo. Never close an issue without approval.
trigger: github.issue.opened
acts_as: team-bot # owner | team-bot
tools:
  - github.search_issues
  - github.add_label
  - github.close_issue
  - slack.post_message
require_approval: [github.close_issue]
budget: { max_steps: 20, max_usd: 0.10, max_seconds: 300 }
```

The authoritative field list will live in `contracts/agent-spec.schema.json`. Rules:

- A published version never changes. A change publishes a new version.
- The platform numbers versions (1, 2, 3, ...); the spec file has no version field.
- Every run records the exact version it used.
- Publishing fails if a tool name does not exist in the gateway or the budget is missing.

## Run states

```
QUEUED ─▶ RUNNING ─▶ WAITING_APPROVAL ─▶ RUNNING ─▶ SUCCEEDED
             │                                 └──▶ FAILED   (budget, step cap, rejected, or error)
             └─ worker crashes ─▶ redelivered ─▶ RUNNING (resumes from last step)
```

A run that keeps crashing moves to a dead-letter queue for inspection.

## Use case 1: GitHub issue triage (demo)

When an issue is opened in a repo, the agent:

1. reads the issue,
2. searches existing issues for duplicates,
3. adds labels,
4. posts a short summary to a Slack channel,
5. asks a human (Slack Approve / Reject buttons) before closing anything as a duplicate.

What happens end to end:

1. GitHub sends a webhook. The platform finds agents whose trigger is `github.issue.opened` and
   creates a queued run for each, pinned to the active version.
2. A worker picks up the run and works through the steps above, saving progress after every step.
3. When the agent wants to close the issue, the run pauses in `WAITING_APPROVAL` and Slack shows
   Approve / Reject buttons.
4. An approver clicks Approve. The run resumes, closes the issue, and ends in `SUCCEEDED`.
5. If a worker crashes at any point, another worker resumes from the last saved step. No label,
   message or close happens twice.

## Use case 2: meeting prep (stretch)

A Notion + Google Calendar agent that prepares notes before meetings. It exists to prove the
platform is generic: a second agent with different tools and triggers and no platform code changes.

## Non-goals

- No billing, self-serve signup or external customers.
- No polished UI; a minimal page or CLI is enough.
- No enterprise-only systems such as Salesforce.
- No custom model training.
