package dev.agentplatform.controlplane.approval;

/**
 * Where an approval request was posted: the channel and the channel's id for the message (Slack:
 * channel id and message {@code ts}). Kept on the run so the decision can update the message.
 */
public record ApprovalMessage(String channel, String messageId) {}
