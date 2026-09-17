# Headless codex Lane

Product direction confirmed with the maintainer in a design interview. The
delivery path was checked with a throwaway prototype (below), and implemented
for #60.

## Problem

The codex Lane refuses to open outside tmux (`resolve_owner`: "must run inside
tmux with an originating tmux pane"). From a plain terminal the Bridge
therefore fails and the Dispatcher falls back to the in-session Matt review, so
a codex review never actually runs there.

## Confirmed constraints

- The Bridge Interface does not change: the same argv, JSON result, exit codes,
  and blocking call. No caller learns whether a reviewer runs in a pane or in
  the background. The Dispatcher (`SKILL.md`) is unchanged.
- One rule, owned by the Bridge: with tmux, a Lane that supports a pane opens
  one; otherwise the reviewer runs in the background. No Lane checks for tmux
  itself, and no configuration switch selects the mode.
- The claude Lane is unchanged; it supports no pane and always runs headless.
- The headless codex path behaves like the claude Lane and reuses as much of
  the existing codex delivery as possible. No progress output, status file, or
  stall detection is added; the existing timeout applies.
- The result discloses no delivery mode.
- A headless reviewer outlives its caller and is collected with
  `--recover-session`, as a claude reviewer is today.

## Design

### The window rule sits in the Bridge

The Bridge decides once whether a tmux window is available (`TMUX` and an
originating pane). `Lane.NEEDS_TMUX` becomes `SUPPORTS_PANE`: a capability,
not a requirement. `lane_owner` fills the tmux half of the owner only when the
Lane supports a pane and a window is available; otherwise the owner is the
worktree alone. A pane review is therefore recovered from its own pane, and a
headless review from any tmux-less caller in the same worktree — the rule the
claude Lane already follows.

A headless codex review and a claude review in one worktree therefore share an
owner, so the owner alone no longer keeps the two Lanes apart. Every record
names the Lane that wrote it (`reviewer`), and recovery and resume match that
as well as the owner. A record written before the field existed is a claude
record if it carries `claudeSessionId`, and a codex record otherwise. The
owner lock is keyed by the owner alone, so a headless codex review and a claude
review in one worktree do not run at the same time, as two claude reviews
there do not today.

### Two delivery Adapters inside the codex Lane

`CodexLane` keeps its Interface (`open`, `discard`, `deliver`, `resume`,
`recover`, `settle`). Behind it sits an internal seam with two Adapters:

| Adapter | Starts | Thread owner | Liveness check |
| --- | --- | --- | --- |
| Pane (existing) | `_pane` in a tmux split: app-server, TUI proxy, TUI | the TUI creates or resumes it | pane exists |
| Headless (new) | a detached `codex app-server` on a runtime socket, in a session of its own | the Bridge calls `thread/start` or `thread/resume` | app-server process alive (`appServerPid` in the record) |

A recorded pid can outlive its app-server and be given to another process, so
the headless Adapter counts a pid as its reviewer, and signals its process
group, only while that process's command line names the reviewer's own
runtime socket. A caller that cannot run `ps` falls back to the pid alone.

Everything after the thread exists is shared: `persist_and_queue_review`,
`ensure_review_delivery`, turn polling, `final_agent_message`, the session
record, `harvest_rollout`, the round cap, and Lifecycle Hooks (the child
launch hook receives an empty tmux target, as for claude). Pane-only checks
(`pane_exists`) become the Adapter's liveness check rather than being skipped.

The headless app-server receives the same model and network overrides as the
pane's. A new thread is started with the caller's sandbox and approval values;
a resume carries no overrides, matching the pane's resume rule.

### MCP startup

In a pane, the TUI proxy records MCP startup notifications from the TUI's
connection. Headless, the notifications arrive on the Bridge's own app-server
connection, so that connection records them in the same format, and the MCP
wait keeps reading the socket between polls, before the Axis Brief is queued.

Checked against codex-cli 0.154.0: `thread/start` and `thread/resume` each
announce the thread's MCP startup on the connection that sent them. Part of it
arrives before the reply and part after it, which is why the wait has to keep
reading.

## Prototype evidence (codex-cli 0.154.0, `TMUX` unset)

Using the Bridge's own `AppServerClient`, `queue_review`, `find_bridge_turn`,
`final_agent_message`, and `harvest_rollout`:

- `thread/start` from the Bridge on a bare app-server; turn 1 `completed`.
- App-server stopped; a second app-server `thread/resume`d the same thread;
  turn 2 `completed` and remembered turn 1.
- The rollout yielded token counters and the resolved model.

## Known limits

- A headless reviewer whose driver died keeps its app-server running, idle once
  the turn ends, until `--recover-session` collects it. A pane in the same
  situation stays open until it is closed.
- A headless reviewer that died together with its driver, leaving no report,
  is not recoverable, as on every Lane: `--recover-session` reports nothing to
  recover, and its runtime directory stays in the temporary directory.

## Documentation changes

- README: the codex Lane needs tmux only to show its reviewer in a pane.
- ADR-0003 (brainstorming): amend "a codex review needs tmux".

## Out of scope

- Claude Code's 10-minute limit on a single Bash call. It affects the claude
  Lane today and is independent of tmux.
