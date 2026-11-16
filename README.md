# govee-claude

Your Govee lamp, and a little orange guy in the menu bar, tell you when Claude needs you.

| Claude… | Lamp | Menu bar |
|---|---|---|
| finished a reply | green fills the 3 lights top → middle → bottom, then back to your color | orange guy wiggles + chat title |
| needs permission, ignored 90s | one quick red flash, then back | guy turns red |
| you reply / approve / click the guy | (already back to normal) | disappears |
| (lamp is off) | nothing | still shows |

The lamp never holds a color. It always goes back to whatever color and brightness you had.
If you use a Govee *scene* or different colors per light, the API can't read that,
so after a flash you get one flat color. Pick a solid color in the app if that bugs you.

Click the guy and it opens that exact chat (Claude desktop app) or the terminal it's in. Right-click shows every waiting chat.

Works with Claude Code (CLI and the Code tab in the desktop app) through hooks. Plain claude.ai web chats have no hooks, so they don't trigger it.

## Install

```bash
git clone https://github.com/Nimay25/govee-claude ~/govee-claude
~/govee-claude/install.sh
python3 ~/govee-claude/govee_claude.py setup
```

`setup` asks for your Govee API key (Govee Home app → Profile → ⚙️ Settings → Apply for API Key; it arrives by email), picks the lamp, and does a test flash.

## Commands

```
python3 govee_claude.py test       # green sweep, then quick red flash
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

The menu bar app doesn't poll. macOS wakes it only when the state file changes. It animates for 20s when something new comes in (red keeps going), then uses 0% CPU. Hooks are async, so they never slow Claude down. The lamp is only called when something changes, a few requests per reply, well under Govee's daily limit.
