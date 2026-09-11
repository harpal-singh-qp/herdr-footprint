<div align="center">

# herdr-footprint

### What each space actually costs you — in disk, and in context.

Two numbers per space in your [herdr](https://herdr.dev) sidebar: how much disk its
git worktree occupies, and how much of its context window the busiest agent in it
has already burned — whichever agent that is.

<img alt="License" src="https://img.shields.io/badge/license-MIT-blue">
<img alt="herdr" src="https://img.shields.io/badge/herdr-%E2%89%A5%200.7.5-5865a3">
<img alt="Platforms" src="https://img.shields.io/badge/Linux%20%C2%B7%20macOS-supported-2ea44f">
<img alt="Runtime" src="https://img.shields.io/badge/bash%20%2B%20python3-no%20toolchain-orange">

<p>
  <a href="#why-youd-want-it">why</a> ·
  <a href="#install">install</a> ·
  <a href="#tokens">tokens</a> ·
  <a href="#how-it-works">how it works</a> ·
  <a href="#configuration">configuration</a> ·
  <a href="#roadmap">roadmap</a>
</p>

</div>

```
  ● api-gateway
    feat/rate-limiting  ↑12
    ◐ 41%     ⛁ 1.4G

  ● web-dashboard
    main  ?23
    ◐ 87%     ⛁ 4.8G

  ● docs-site
    main  ✓
    ◐ --      ⛁ 62M
```

## Why you'd want it

You can see which agent is blocked. You cannot see which space is about to run out
of context, or which one is quietly holding 13 GB of `node_modules` you stopped
needing three branches ago.

Every other sidebar plugin reports **per machine** (CPU, RAM, free disk) or **per
agent** (tokens, rate limits). Those are the wrong units for a decision you make per
space: *is this one finished with me?*

- **`◐ 94%`** — that space is one long turn from a compaction you did not plan.
- **`⛁ 13.2G`** — that space is why your disk alert fired.

Both answers, without focusing the tab.

## Install

```bash
herdr plugin install harpal-singh-qp/herdr-footprint
```

No toolchain, no compile step — bash and python3, both of which you already have.

Then reference the tokens in your space rows in `~/.config/herdr/config.toml`:

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

That is the whole setup. The poller starts itself on the next herdr launch, or
immediately with `herdr plugin action invoke start --plugin footprint`.

### Colour by value (herdr 0.9.0+)

On 0.9.0 and later, tokens can restyle themselves by value — so a space turns yellow
as it fills and red before it bites:

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
| `$ctx` | `◐ 84%` | Largest context-window share among the space's agent panes |

Both fall back to `--` instead of vanishing, so a configured row never collapses.

## How it works

A `[[startup]]` hook detaches a poll loop, because herdr's startup hook is one-shot.
Each cycle:

1. Lists spaces, resolving each one's directory from its first pane's `cwd`.
2. Resolves that to the **git worktree root**. Several spaces commonly live in
   subdirectories of one checkout, and the worktree is what occupies disk — measuring
   `cwd` would report a subdirectory as if it were the whole cost.
3. Pushes `$disk` (from cache) and `$ctx` via `workspace report-metadata`.
4. Re-measures **exactly one** worktree: the stalest one past its TTL.

Step 4 is the design. A 13 GB checkout takes ~14 s to walk and a 2 GB one ~5 s, so
measuring every space every tick would keep a core busy permanently. One walk per
cycle keeps the sidebar populated without this plugin ever being why your fan spins.

### Where context comes from

Two sources, in order:

1. **A `context` metadata token on the pane.** Usage plugins such as
   [herdr-agent-usage](https://github.com/senna-lang/herdr-agent-usage) publish one
   per pane for Claude, Codex, OpenCode, Grok, Pi, omp, Cursor and direct API
   backends. Preferring it means footprint covers every provider those plugins
   cover, and never has to track a transcript format it does not own.
2. **Claude's own transcript**, so a space still reports something useful when no
   usage plugin is installed.

Source 2 has a wrinkle: Claude transcripts record token usage but never the context
window. A session that has already passed 200k tokens proves it is on the 1M window,
so the window is inferred rather than assumed. Pin it with `FOOTPRINT_CONTEXT_WINDOW`
if you would rather be explicit. Source 1 needs none of this — the publishing plugin
already knows the window.

## Configuration

`$HERDR_PLUGIN_CONFIG_DIR/config.env` — shell syntax, every key optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `FOOTPRINT_CADENCE_SEC` | `60` | Seconds between push cycles |
| `FOOTPRINT_REMEASURE_SEC` | `900` | Minimum age before a worktree is walked again |
| `FOOTPRINT_CONTEXT_WINDOW` | `0` | `0` infers; set e.g. `200000` to pin |
| `FOOTPRINT_DISK_ICON` | `⛁` | |
| `FOOTPRINT_CTX_ICON` | `◐` | |

## Actions

| Action | Does |
| --- | --- |
| `footprint.refresh` | Run one measurement cycle now |
| `footprint.start` | Start the poller |
| `footprint.stop` | Stop the poller |

Bind one if you like:

```toml
[[keys.command]]
key = "prefix+shift+k"
type = "shell"
command = "herdr plugin action invoke refresh --plugin footprint"
description = "footprint: refresh"
```

## Reliability

- **No `set -e` in the loop.** One failed measurement never kills the poller.
- **Pidfile guard.** A herdr restart cannot stack pollers.
- **Tokens carry a TTL of three cycles.** A stopped poller fades its numbers out
  rather than leaving a stale figure on screen forever.
- **Read-only.** v0.1 measures. It never deletes anything.

State lives in `$HERDR_PLUGIN_STATE_DIR` — a size cache, a pidfile, and a log.

## Roadmap

- **v0.2** — an overlay pane breaking the machine down by what is *reclaimable*:
  Docker images, volumes and build cache, agent transcripts, stale worktrees, dead
  `node_modules` — each classified SAFE / REVIEW / BLOCKED.
- **v0.3** — reclaim, itemised, behind a confirmation, with a git bundle taken before
  any worktree or branch is removed. Never a blanket prune.

## Requirements

herdr ≥ 0.7.5 · bash · python3 · git · Linux or macOS

**Recommended companion:** a usage plugin such as
[herdr-agent-usage](https://github.com/senna-lang/herdr-agent-usage). `$disk` works on
its own, but `$ctx` reads the `context` token such a plugin publishes — that is what
extends it to Codex, OpenCode, Grok, Pi, omp, Cursor and API backends. Without one,
`$ctx` falls back to Claude transcripts and any non-Claude pane shows `--`.

## Contributing

Issues and PRs welcome. The plugin is four small scripts; `bin/collect.sh` is where
almost everything happens.

## License

MIT
