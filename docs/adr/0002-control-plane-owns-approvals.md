# 0002. The control plane sends and receives approvals

- Status: Accepted
- Date: 2026-11-16

## Context

When a run hits a tool in `require_approval`, someone has to post Approve / Reject buttons, and the
click comes back as a Slack callback to the control plane. The first plan had the worker post the
buttons, which split one feature across two services and gave the worker a Slack dependency.

## Decision

The worker only reports `WAITING_APPROVAL` with the pending call through the internal API. The
control plane posts the buttons through an `ApprovalChannel` port (Slack adapter first), receives
the callback, records the decision, and re-enqueues the run.

## Consequences

- Sending and receiving approvals live in one service, with one Slack bot credential.
- The worker has no Slack code; adding email or web approvals touches only the control plane.
- Approval notifications are separate from the agent's own `slack.post_message` tool, which still
  goes through the tool gateway.
