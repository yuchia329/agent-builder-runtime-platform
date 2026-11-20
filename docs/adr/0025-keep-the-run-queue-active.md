# 0025. Keep the run queue active with an hourly scheduled call

- Status: Proposed
- Date: 2026-11-20

## Context

Workers scale from 0 on a CloudWatch alarm over the run queue's `ApproximateNumberOfMessagesVisible`
(ADR 0007, ADR 0024). SQS sends a queue's metrics only while the queue is "active": AWS counts a
queue as active for up to six hours after it holds a message or any call reaches it. After that
it stops sending metrics, and when the queue wakes "a delay of up to 15 minutes occurs in
CloudWatch metrics" (Amazon SQS Developer Guide, Monitoring queues using CloudWatch).

With the workers at 0 nothing calls the queue, so every quiet period longer than six hours ends
with a slow start. Measured on 2026-11-20 (M7 load driver, 100 runs after a quiet day): the queue's
first metric point came about 4 minutes after the runs were queued, the alarm fired about 6
minutes after, and the tasks were running about 20 s later. With an active queue, a run waited
0.1 s (p50) for a running worker, and the 2026-11-19 cold stub run took about 3.5 minutes in all.
The first real issue after a quiet day waits the same way.

Options looked at:

- **Start a worker on enqueue:** the control plane sets the service to 1 task when it queues a run
  and none are running. Fastest (about 30-60 s), but the control plane would change the ECS
  service: a new permission and code in another component, and a second path that scales workers
  next to the alarms.
- **Keep the queue active with a scheduled call:** EventBridge Scheduler calls the queue regularly.
  Infra only and free; the first run still waits for the alarm's one-minute period and CloudWatch's
  lag (about 2-3 minutes).
- **Accept it:** demos after a quiet day start about 6 minutes late (up to 15 documented).

## Decision

- An EventBridge Scheduler schedule (`keep_queue_active`) calls the run queue every hour, well
  inside the six hours.
- Scheduler's universal target cannot call read-only actions (names starting with `get`, `list`,
  `receive` and others), so `GetQueueAttributes` is not possible. The schedule calls `TagQueue`
  with the tag the queue already has (`project`): no change to the queue, nothing for Terraform
  to see. `SendMessage` is not used: a message would start a run and a worker.
- The scheduler role gets `sqs:TagQueue` on the run queue only.

## Consequences

- The run queue sends metrics every minute at all times, so scale-out from 0 waits only for the
  alarm and the task start. Check: after more than six hours with no run, the queue's metric
  points keep coming and the first run starts within about 3 minutes.
- Cost: about 720 Scheduler calls and 720 SQS calls a month, inside the free tiers; SQS metrics are
  free. Nothing bills by the hour (ADR 0007 holds).
- The dead-letter queue is not kept active: nothing scales on it.
- If 2-3 minutes is still too slow, starting a worker on enqueue (control plane) remains an option;
  it would need its own ADR.
- If the queue's `project` tag changes in Terraform, the schedule's input changes with it (it reads
  `var.project`).
