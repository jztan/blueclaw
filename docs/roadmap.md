# BlueClaw Roadmap

Upcoming work only. Released changes belong in [CHANGELOG.md](../CHANGELOG.md). Remove completed milestones from this roadmap when they ship.

**Next:** v3.1 — Reliable Execution and Feedback. Version labels are planning targets, not release declarations or ship dates.

## v3.1 — Reliable Execution and Feedback (planned)

Make everyday runs dependable: stopping a run has a clear outcome, partial work remains inspectable, large tool outputs can be retrieved after truncation, and conversation summaries preserve corrections and unfinished tasks. The playground shows current activity and lets users read earlier messages without being pulled back to the bottom. The trace CLI makes captured artifacts easier to find.

## v3.2 — Context Efficiency and Transcript Readability (planned)

Keep tool-heavy conversations manageable by budgeting combined tool output while retaining access to full results, bounding repeated summarization failures, and folding successful tool sequences into expandable activity summaries. Validate that context savings preserve task success before enabling new limits by default.

## v3.3 — Queued Follow-ups (tentative)

Let users queue, edit, and remove follow-up messages while a run is active. Messages execute in conversation order, with clear behavior when a run stops or fails and when the user changes conversations. This milestone follows the cancellation and activity improvements in v3.1.

---

## Planned (unversioned)

Capability expansion follows the reliability milestones above. Assign versions when concrete use cases and scope justify the added complexity.

### Subagent support

Delegate focused tasks to subagents with their own tools and memory while keeping the parent responsible for user communication. Delegated work should inherit the same isolation boundary and remain inspectable alongside the parent run.

### Multi-Channel Runtime

Extend BlueClaw to additional messaging channels with consistent authentication, conversation routing, and persistence. Add Slack and Discord integrations and adapt the existing Telegram bridge to the shared channel interface.

---

## Explicitly Deferred

| Feature | Reason |
|---|---|
| Task scheduling | Can be a skill, not core |
| Browser automation | Can be an MCP server, not core |
| OpenTelemetry export | No current need; revisit when external observability is required |
