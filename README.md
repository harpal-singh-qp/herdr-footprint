# herdr-diskspace

**What a space costs you** — disk footprint and context usage, per space, in the Herdr sidebar.

```
◐ 84%   ⛁ 2.1G      portal-content-design
◐ 93%   ⛁ 46.2M     fm-captain
```

Every other Herdr sidebar plugin reports machine-level metrics or agent-level
metrics. This one reports **per space**: how much disk the space's git worktree
occupies, and how much of its context window the busiest agent in it has burned.
Those are the two numbers that decide whether a space is finished with you.

## Install

```bash
herdr plugin install <owner>/herdr-diskspace
```

Then add the tokens to your space rows in `~/.config/herdr/config.toml`:

```toml
[ui.sidebar.spaces]
rows = [
  ["state_icon", "workspace"],
  ["branch", "git_status"],
  [{ token = "$ctx", fg = "#89b4fa" }, { token = "$disk", fg = "#9399b2" }],
]
```

```bash
herdr server reload-config
```

On Herdr 0.9.0+ you can colour by value instead of flat text:

```toml
  [{ token = "$ctx", fg = "#89b4fa", rules = [
      { gt = 70, fg = "#f9e2af" },
      { gt = 85, fg = "#f38ba8", bold = true } ] },
   { token = "$disk", fg = "#9399b2" }],
```

## Tokens

| Token | Example | Meaning |
| --- | --- | --- |
| `$disk` | `⛁ 2.1G` | Size of the space's git worktree root (`du -sx`) |
| `$ctx` | `◐ 84%` | Largest context-window share among the space's Claude panes |

Both fall back to `--` rather than vanishing, so a row never collapses.

## How it works

A `[[startup]]` hook detaches a poll loop, because Herdr's startup hook is
one-shot. Each cycle the loop:

1. Lists spaces, and resolves each one's directory from its first pane's `cwd`.
2. Resolves that to the **git worktree root** — several spaces commonly live in
   subdirectories of one checkout, and the worktree is what occupies disk.
3. Pushes `$disk` (from cache) and `$ctx` via `workspace report-metadata`.
4. Re-measures **exactly one** worktree per cycle: the stalest one past its TTL.

Step 4 is the whole design. A 13 GB checkout takes ~14 s to walk, so measuring
every space every tick would keep a core busy forever. One walk per cycle keeps
the sidebar populated without the plugin ever being the reason your fan spins.

### Context window inference

Claude transcripts record token usage but never the context window. A session
that has already passed 200k tokens proves it is on the 1M window, so the window
is inferred from observed usage rather than guessed. Pin it with
`DISKSPACE_CONTEXT_WINDOW` if you prefer.

## Configuration

`$HERDR_PLUGIN_CONFIG_DIR/config.env` — shell syntax, all optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISKSPACE_CADENCE_SEC` | `60` | Seconds between push cycles |
| `DISKSPACE_REMEASURE_SEC` | `900` | Minimum age before a worktree is walked again |
| `DISKSPACE_CONTEXT_WINDOW` | `0` | `0` infers; set e.g. `200000` to pin |
| `DISKSPACE_DISK_ICON` | `⛁` | |
| `DISKSPACE_CTX_ICON` | `◐` | |

## Actions

| Action | Does |
| --- | --- |
| `diskspace.refresh` | Run one cycle now |
| `diskspace.start` / `diskspace.stop` | Control the poller |

## Reliability

- No `set -e` in the loop — one failed measurement never kills the poller.
- Pidfile guard, so a Herdr restart cannot stack pollers.
- Tokens carry a TTL of three cycles: a stopped poller fades its numbers out
  instead of leaving a stale figure on screen.
- Read-only. v0.1 measures and never deletes.

## Roadmap

- **v0.2** — an overlay pane breaking the machine down by what is *reclaimable*:
  Docker images, volumes and build cache, agent transcripts, stale worktrees,
  dead `node_modules` — each classified SAFE / REVIEW / BLOCKED.
- **v0.3** — reclaim, itemised, behind a confirmation, with a git bundle taken
  before any worktree or branch is removed. Never a blanket prune.

## Requirements

Herdr ≥ 0.7.5 · bash · python3 · git · Linux or macOS

## License

MIT
