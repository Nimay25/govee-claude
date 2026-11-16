# govee-claude

Your Govee lamp, and a little orange guy in the menu bar, tell you when Claude needs you.

| Claude… | Lamp | Menu bar |
|---|---|---|
| finished a reply | warm gold | orange guy wiggles + chat title |
| needs permission, unanswered 90s | slow red breathe, then red | guy turns red |
| you reply / approve / click the guy | back to whatever it was | disappears |
| (lamp is off) | nothing | still shows |

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
python3 govee_claude.py test       # gold -> red -> back
python3 govee_claude.py status
python3 govee_claude.py clear      # everything back to normal
python3 govee_claude.py pause      # stop reacting (resume to undo)
python3 govee_claude.py uninstall  # remove the hooks
```

Settings live in `~/.config/govee-claude/config.json` (`gold`, `red`, `red_after_sec`, `gold_max_min`, `min_brightness`). Logs: `~/.config/govee-claude/log.txt`.

## Battery

The menu bar app doesn't poll. macOS wakes it only when the state file changes. It animates for 20s when something new comes in (red keeps going), then uses 0% CPU. Hooks are async, so they never slow Claude down. The lamp is only called when something changes, a few requests per reply, well under Govee's daily limit.
