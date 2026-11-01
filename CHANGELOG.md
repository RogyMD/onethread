# Changelog

## 1.0.0 (2026-11-01)

The first stable release. Onethread lets one Claude Code or Codex conversation
coordinate local coding tasks while separate workers run in their own Git
worktrees. Onethread itself has no hosted service, telemetry, or account; the
Claude Code or Codex CLI you choose still needs its own provider setup.

### Included

- A prioritized task queue with dependencies and per-task agent sessions.
- Background workers, process recovery, resource leases, and worktree cleanup.
- Review and task choices in the orchestrator chat, with explicit owner approval
  for consequential actions.
- iTerm2 task tabs and notifications; Apple's Terminal can mark its tab with a
  bell. The notification itself has no buttons: choices appear in chat.
- A local SQLite database, synthetic demo, and automated test suite.

### Requirements and limits

- macOS, Python 3.9+, Git, and at least one of Claude Code or Codex CLI.
- iTerm2 is recommended for opening task tabs and receiving notifications.
- Codex cannot be woken by onethread; it reports new items on your next message.
- Workers can run a repository's test and build scripts. Only use repositories
  you trust; see [HOW-IT-WORKS.md](HOW-IT-WORKS.md) for the safety details.
- The current real-tool verification status and remaining checks are in
  [docs/STATUS.md](docs/STATUS.md).

Install from source using the steps in [README.md](README.md).
