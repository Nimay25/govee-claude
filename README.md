# govee-claude

Your Govee lamp, and a little orange guy in the menu bar, tell you when Claude needs you.

**Menu bar:** the Claude logo plus one traffic light per Claude agent you have going:

- 🟡 **yellow**: working
- 🟢 **green**: done, waiting for you
- 🔴 **red**: stopped, needs something from you (a permission prompt)

Click it for the list of agents, with how long each one has been done or waiting. Pick one to jump straight to that chat. The logo does a quick spin when one finishes or needs you.

**Lamp:** when an agent finishes, green fills the 3 lights top → middle → bottom, then it goes back to your color. When it stops to **ask you a question** (a question at the end of its reply, or a question box), the same sweep happens in **yellow** and the lamp **stays yellow until you answer** (then it goes back; after an hour with no answer it gives up and goes back anyway). A **permission prompt** gets it in **red**. A permission prompt left unanswered for 90s gets one extra quick red flash. If the lamp is off, it does nothing (the menu bar still works).

Apart from a waiting question, the lamp never holds a color. It always goes back to whatever color and brightness you had.
If you use a Govee *scene* or different colors per light, the API can't read that,
so after a flash you get one flat color. Pick a solid color in the app if that bugs you.

Works with Claude Code (CLI and the Code tab in the desktop app) through hooks, and with **claude.ai in Chrome** through a tiny extension in `web/extension`. Load it once: `chrome://extensions` → Developer mode → Load unpacked → pick `web/extension`. After pulling an update, hit the ↻ reload button on the extension in `chrome://extensions`. (Chat tabs inside the Claude desktop app itself aren't covered.)

## Install

```bash
git clone https://github.com/Nimay25/govee-claude ~/govee-claude
~/govee-claude/install.sh
python3 ~/govee-claude/govee_claude.py setup
```

`setup` asks for your Govee API key (Govee Home app → Profile → ⚙️ Settings → Apply for API Key; it arrives by email), picks the lamp, and does a test flash.

## Commands

```
python3 govee_claude.py test       # green, yellow, then red sweep
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

## License

[MIT](LICENSE)

## Optional: report chat status to an HQ server

If `~/.config/rallo-hq/agent.env` exists (keep it `chmod 600`; it is never in this repo), each Claude Code chat's state
(working, done, waiting on a question or a permission prompt) is also posted to your HQ server whenever it changes, plus a
"still working" note every 10 minutes for long turns. It runs in the background with a 2-second timeout, so the lamp and Claude never
wait on it, and if HQ is down the only trace is one line in `log.txt`. Only the first 200 characters of the last message are sent.
The file has two lines:

```
HQ_STATUS_URL=https://your-hq-api-host
HQ_STATUS_TOKEN=a status-only key from your HQ
```

Delete the file to turn it off. claude.ai web chats aren't reported yet.

