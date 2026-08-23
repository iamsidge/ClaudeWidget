# ClaudeWidget

A desktop widget showing your Anthropic rate-limit usage — the 5-hour and
7-day utilisation windows, at a glance, without opening a browser.

Two implementations from one idea:

| | Platform | UI | Requires |
|---|---|---|---|
| `claude_widget.py` | Linux | Floating GTK3 window, drawn with Cairo | `python3-gi` |
| `claude_widget_mac.py` | macOS | Menu-bar item | `rumps` |

## How it works

Every 10 seconds it sends a deliberately minimal request to
`POST /v1/messages` — Haiku, `max_tokens: 1`, a one-character prompt — and
reads the rate-limit headers off the response:

```
anthropic-ratelimit-unified-5h-utilization
anthropic-ratelimit-unified-7d-utilization
anthropic-ratelimit-unified-5h-reset
anthropic-ratelimit-unified-7d-reset
anthropic-ratelimit-unified-status
```

There's no usage endpoint to query, so the headers on a real (tiny) call are
the source. Each poll costs a couple of tokens.

## Authentication

Three sources, tried in order:

1. **Claude Code's OAuth token** — `~/.claude/.credentials.json`, sent as
   `Authorization: Bearer`. Re-read on every poll, so it survives token
   rotation across sleep/wake.
2. **`ANTHROPIC_API_KEY`** in the environment, sent as `x-api-key`.
3. **`~/.config/claude-widget/api_key`**, same header.

## Run

**Linux:**

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

```bash
./claude-widget.sh
```

Forces the X11 GDK backend — the window is undecorated and positions itself,
which Wayland does not allow. Left-drag to move it, right-click to quit.

**macOS:**

```bash
./claude-widget-mac.sh
```

Installs `rumps` and pinned `pyobjc` 9.2 on first run if they're missing, then
launches the menu-bar item. Click it for the detail view.

## Autostart

- **Linux** — add `claude-widget.sh` to your desktop environment's startup applications.
- **macOS** — System Settings → General → Login Items.

## Display

The Linux widget draws a donut for 5-hour utilisation and a pill bar for the
7-day window, colour-graded green → orange → red as you approach the limit,
with the reset time beneath each. The macOS version shows the 5-hour figure in
the menu bar and the rest in the dropdown.
