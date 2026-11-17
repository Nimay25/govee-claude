# govee-claude

Your Govee lamp, and a little orange guy in the menu bar, tell you when Claude needs you.

**Menu bar:** the Claude logo plus one traffic light per Claude agent you have going:

- 🟡 **yellow**: working
- 🟢 **green**: done, waiting for you
- 🔴 **red**: stopped, needs something from you (a permission prompt)

Click it for the list of agents, with how long each one has been done or waiting. Pick one to jump straight to that chat. The logo does a quick spin when one finishes or needs you.

**Lamp:** when an agent finishes, green fills the 3 lights top → middle → bottom, then it goes back to your color. When it stops to **ask you something** (a question at the end of its reply, a question box, or a permission prompt), the same sweep happens in **red**. A permission prompt left unanswered for 90s gets one extra quick red flash. If the lamp is off, it does nothing (the menu bar still works).

The lamp never holds a color. It always goes back to whatever color and brightness you had.
If you use a Govee *scene* or different colors per light, the API can't read that,
so after a flash you get one flat color. Pick a solid color in the app if that bugs you.

Works with Claude Code (CLI and the Code tab in the desktop app) through hooks, and with **claude.ai in Chrome** through a tiny extension in `web/extension`. Load it once: `chrome://extensions` → Developer mode → Load unpacked → pick `web/extension`. (Chat tabs inside the Claude desktop app itself aren't covered.)

## Install

```bash
git clone https://github.com/Nimay25/govee-claude ~/govee-claude
~/govee-claude/install.sh
python3 ~/govee-claude/govee_claude.py setup
```

`setup` asks for your Govee API key (Govee Home app → Profile → ⚙️ Settings → Apply for API Key; it arrives by email), picks the lamp, and does a test flash.

## Commands

```
python3 govee_claude.py test       # green sweep, then red sweep
python3 govee_claude.py sweep      # just the green sweep
python3 govee_claude.py color deep purple 44   # set the lamp (name, #hex or r,g,b, + brightness)
python3 govee_claude.py lock       # always come back to the current color after a flash
python3 govee_claude.py unlock     # come back to whatever the lamp was on
python3 govee_claude.py status
python3 govee_claude.py clear      # clear the menu bar
python3 govee_claude.py pause      # stop reacting (resume to undo)
python3 govee_claude.py uninstall  # remove the hooks
```

Settings live in `~/.config/govee-claude/config.json` (`green`, `sweep_order`, `sweep_step_sec`, `sweep_hold_sec`, `red`, `red_after_sec`, `flash_sec`, `min_brightness`). Logs: `~/.config/govee-claude/log.txt`.

## Battery

The menu bar app doesn't poll. macOS wakes it only when the state file changes. The logo spins for about 2 seconds when an agent finishes or needs you; otherwise it uses 0% CPU. Hooks are async, so they never slow Claude down. The lamp is only called when something changes, a few requests per reply, well under Govee's daily limit.
