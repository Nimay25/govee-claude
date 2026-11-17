#!/usr/bin/env python3
"""govee-claude: your Govee lamp tells you when Claude needs you.

  Claude finished          -> green fills the lamp top -> middle -> bottom, then back to your color
  Claude asks you something / needs permission -> same sweep in red, then back
  Permission prompt you've ignored for 90s -> one quick red flash, then back
  Menu bar                 -> Claude logo + a traffic light per agent (yellow working,
                              green done, red needs you); click to jump to that chat
  Lamp is off              -> lamp does nothing (menu bar still works)

No dependencies. Python 3 stdlib only.
"""
import fcntl
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

DIR = os.environ.get("GOVEE_CLAUDE_DIR") or os.path.expanduser("~/.config/govee-claude")
CONFIG = os.path.join(DIR, "config.json")
STATE = os.path.join(DIR, "state.json")
LOCK = os.path.join(DIR, "lock")
LOG = os.path.join(DIR, "log.txt")
PAUSED = os.path.join(DIR, "paused")
FAKE = os.path.join(DIR, "fake-lamp.json")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
API = "https://openapi.api.govee.com/router/api/v1"
SCRIPT = os.path.realpath(__file__)

DEFAULTS = {
    "gold": [255, 170, 0],     # "Claude's done / needs you"
    "red": [255, 25, 10],      # "Claude is blocked and you haven't looked"
    "red_after_sec": 90,       # unanswered permission prompt -> one red flash after this long
    "flash_sec": 2.5,          # how long the red flash lasts before your color comes back
    "green": [10, 255, 0],     # "Claude's done" sweep color (traffic-light green)
    "sweep_order": [0, 1, 2],  # which light goes first (top -> middle -> bottom)
    "sweep_step_sec": 0.45,    # gap between lights
    "sweep_hold_sec": 1.0,     # all three green this long, then back
    "min_brightness": 30,      # so gold is visible even if the lamp is dimmed way down
}

COLOR = "devices.capabilities.color_setting"
RANGE = "devices.capabilities.range"
SEGMENT = "devices.capabilities.segment_color_setting"

NAMED = {
    "deep purple": [70, 0, 255], "purple": [120, 30, 255], "violet": [150, 60, 255],
    "pink": [255, 60, 160], "red": [255, 20, 10], "orange": [255, 90, 0],
    "gold": [255, 170, 0], "green": [10, 255, 0], "teal": [0, 200, 170],
    "blue": [0, 0, 255], "ice": [120, 200, 255], "white": [255, 255, 255],
}
NAMED_WHITES = {"warm": 2700, "warm white": 2700, "soft white": 3500, "daylight": 6500}
FAKE_MODE = bool(os.environ.get("GOVEE_FAKE"))


# ---------- small helpers ----------

def log(msg):
    os.makedirs(DIR, exist_ok=True)
    try:
        if os.path.getsize(LOG) > 200_000:
            os.replace(LOG, LOG + ".old")
    except OSError:
        pass
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data, private=False):
    os.makedirs(DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def cfg():
    c = dict(DEFAULTS)
    c.update(load(CONFIG, {}))
    c["api_key"] = os.environ.get("GOVEE_API_KEY") or c.get("api_key")
    return c


class locked:
    def __enter__(self):
        os.makedirs(DIR, exist_ok=True)
        self.f = open(LOCK, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ---------- Govee API ----------

def call(c, method, path, body=None):
    if FAKE_MODE:
        return fake_call(path, body)
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Govee-API-Key": c["api_key"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def payload(c, **extra):
    return {"requestId": str(uuid.uuid4()),
            "payload": {"sku": c["sku"], "device": c["device"], **extra}}


def lamp_state(c):
    """{'on', 'online', 'rgb', 'kelvin', 'brightness'} or None if we can't tell."""
    try:
        resp = call(c, "POST", "/device/state", payload(c))
        caps = {x["instance"]: x.get("state", {}).get("value")
                for x in resp["payload"]["capabilities"]}
    except Exception as e:  # network down, bad key, rate limit... stay quiet
        log(f"state failed: {e}")
        return None
    return {
        "on": caps.get("powerSwitch") == 1,
        "online": caps.get("online") is not False,
        "rgb": caps.get("colorRgb") or 0,
        "kelvin": caps.get("colorTemperatureK") or 0,
        "brightness": caps.get("brightness") or 50,
    }


def control(c, type_, instance, value):
    try:
        call(c, "POST", "/device/control",
             payload(c, capability={"type": type_, "instance": instance, "value": value}))
    except Exception as e:
        log(f"control {instance}={value} failed: {e}")


def rgb_int(rgb):
    r, g, b = rgb
    return (r << 16) | (g << 8) | b


def set_rgb(c, rgb):
    control(c, COLOR, "colorRgb", rgb_int(rgb) if isinstance(rgb, list) else rgb)


def set_brightness(c, v):
    control(c, RANGE, "brightness", max(1, min(100, int(v))))


# ---------- the actual behaviour ----------
# The lamp never *holds* a color: it flashes for a moment and goes straight back
# to whatever you had. The menu bar is what keeps track of who's waiting.

def new_state():
    return {"agents": {}, "vibe": None}


def restore(c, s):
    if s["kelvin"]:
        control(c, COLOR, "colorTemperatureK", s["kelvin"])
    else:
        set_rgb(c, s["rgb"])
    set_brightness(c, s["brightness"])


def set_segment(c, seg, rgb):
    control(c, SEGMENT, "segmentedColorRgb", {"segment": [seg], "rgb": rgb_int(rgb)})


def home_color(c, st):
    """The color to come back to: your locked color, or whatever the lamp is on right now."""
    if c.get("locked"):
        return c["locked"]
    s = lamp_state(c)
    ours = {rgb_int(c["gold"]), rgb_int(c["red"]), rgb_int(c["green"])}
    if s and not s["kelvin"] and s["rgb"] in ours and st.get("vibe"):
        return st["vibe"]  # Govee still reports one of our colors (it lags) -> your last real one
    if s:
        st["vibe"] = s
        save(STATE, st)
    return s


def flash(kind):
    """kind = "done":  green fills top -> middle -> bottom, then your color comes back.
       kind = "needs": same sweep in red (Claude asked you something / wants permission).
       kind = "red":   one quick red flash (reminder), then your color comes back."""
    c = cfg()
    if not c.get("device"):
        return
    with locked():
        s = lamp_state(c)
        if not s or not s["on"] or not s["online"]:
            log("lamp off/offline, menu bar only")
            return
        home = home_color(c, load(STATE, new_state()))
        log(f"flash {kind}")
        if home["brightness"] < c["min_brightness"]:
            set_brightness(c, c["min_brightness"])
        if kind in ("done", "needs"):
            color = c["green"] if kind == "done" else c["red"]
            for seg in c["sweep_order"]:
                set_segment(c, seg, color)
                time.sleep(c["sweep_step_sec"])
            time.sleep(c["sweep_hold_sec"])
        else:
            set_rgb(c, c["red"])
            time.sleep(c["flash_sec"])
        restore(c, home)


def set_color(text):
    """`color purple` / `color #5800dc` / `color 88,0,220` / `color warm` (+ optional brightness)."""
    c = cfg()
    parts = text.split()
    bright = int(parts.pop()) if len(parts) > 1 and parts[-1].isdigit() else None
    name = " ".join(parts).lower().strip()
    s = lamp_state(c) or {"brightness": 50}
    home = {"on": True, "online": True, "rgb": 0, "kelvin": 0,
            "brightness": bright or s["brightness"]}
    if name in NAMED_WHITES:
        home["kelvin"] = NAMED_WHITES[name]
    elif name in NAMED:
        home["rgb"] = rgb_int(NAMED[name])
    elif name.startswith("#") and len(name) == 7:
        home["rgb"] = int(name[1:], 16)
    elif name.count(",") == 2:
        home["rgb"] = rgb_int([int(x) for x in name.split(",")])
    else:
        sys.exit(f"Don't know '{name}'. Try: {', '.join(list(NAMED) + list(NAMED_WHITES))}, #rrggbb, or r,g,b")
    with locked():
        restore(c, home)
        st = load(STATE, new_state())
        st["vibe"] = home
        save(STATE, st)
    if c.get("locked"):  # you picked it on purpose -> that's the new locked color
        conf = load(CONFIG, {})
        conf["locked"] = home
        save(CONFIG, conf, private=True)
    print(f"Lamp set to {name}" + (f" at {home['brightness']}%" if bright else "") +
          (" (locked)" if c.get("locked") else ""))


def lock(on):
    conf = load(CONFIG, {})
    if on:
        st = load(STATE, new_state())
        conf["locked"] = st.get("vibe") or lamp_state(cfg())
        print("Locked: after every flash the lamp goes back to this exact color.")
    else:
        conf.pop("locked", None)
        print("Unlocked: after a flash the lamp goes back to whatever it was on.")
    save(CONFIG, conf, private=True)


def spawn(*args):
    """Run in the background so a hook timeout can never cut a flash off half way."""
    subprocess.Popen([sys.executable, SCRIPT, *args],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True,
                     env=os.environ.copy())


# Every Claude agent gets a traffic light in the menu bar:
#   working = yellow, done (waiting for you) = green, needs you (permission) = red
WORKING, DONE, NEEDS = "working", "done", "needs"
FORGET_AFTER = {WORKING: 30 * 60, DONE: 3 * 3600, NEEDS: 3 * 3600}  # stale agents drop off


def prune(st):
    now = time.time()
    st["agents"] = {k: a for k, a in st.get("agents", {}).items()
                    if now - a.get("last", 0) < FORGET_AFTER.get(a.get("status"), 3600)}


def set_status(sid, status, data, need, info=None):
    """Record what this agent is doing. Returns a fresh token if the status changed."""
    if os.path.exists(PAUSED):
        return None
    now = time.time()
    a = load(STATE, new_state()).get("agents", {}).get(sid)
    if a and status == a.get("status") == WORKING and now - a.get("last", 0) < 60:
        return None  # fast path: PostToolUse fires constantly, nothing new to say
    with locked():
        st = load(STATE, new_state())
        prune(st)
        a = st["agents"].get(sid) or {"first": now}
        changed = a.get("status") != status
        if changed or "title" not in a:
            a.update(info or chat_info(data, need))
        a["last"] = now
        if changed:
            a.update(status=status, since=now, token=uuid.uuid4().hex)
            log(f"{status} {sid[:8]} {a.get('title', '')[:30]}")
        st["agents"][sid] = a
        save(STATE, st)
    return a["token"] if changed else None


def clear(sid=None):
    """Chat closed (sid) or `clear` (None = everything)."""
    st = load(STATE, new_state())
    if sid is not None and sid not in st.get("agents", {}):
        return
    with locked():
        st = load(STATE, new_state())
        if sid is None:
            st["agents"] = {}
        else:
            st.get("agents", {}).pop(sid, None)
        save(STATE, st)


def watch(sid, token):
    """Permission prompt still unanswered after a while -> one short red flash."""
    time.sleep(cfg()["red_after_sec"])
    a = load(STATE, new_state()).get("agents", {}).get(sid)
    if a and a.get("token") == token and a.get("status") == NEEDS:
        flash("red")


def short(text, n=90):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def chat_info(data, need):
    """What the menu bar shows, and where a click should take you."""
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID", "")  # set inside the Claude desktop app
    title = ""
    if host:
        import glob
        for f in glob.glob(os.path.expanduser(
                f"~/Library/Application Support/Claude/claude-code-sessions/*/*/{host}.json")):
            title = load(f, {}).get("title", "")
    title = title or os.path.basename(data.get("cwd") or os.getcwd()) or "Claude"
    return {
        "title": short(title, 60),
        "need": short(need),
        "link": f"claude://code/needs-input?session={host}&source=govee_claude" if host else "",
        "app": os.environ.get("__CFBundleIdentifier", ""),  # terminal / editor fallback
    }


def asks(text):
    """Does this message end by asking you something?"""
    tail = (text or "").strip().split("\n")[-1].rstrip(" *_`)\"'\u201d\u2019")
    return tail.endswith("?")


def final_turn(data):
    """(Claude's final message, the question it asked you or "") for this turn, from the transcript."""
    text = data.get("last_assistant_message") or ""
    question = text.strip().split("\n")[-1] if asks(text) else ""
    try:
        with open(data["transcript_path"], "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 300_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except (OSError, KeyError, TypeError):
        lines = []
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        content = obj.get("message", {}).get("content", [])
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content)}]
        if obj.get("type") == "user":
            if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
                continue
            break  # reached your last message: the turn is over
        if obj.get("type") != "assistant":
            continue
        for x in content:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "text" and x.get("text", "").strip() and not text:
                text = x["text"]
                if asks(text):
                    question = text.strip().split("\n")[-1]
            # the desktop app's next-step box ("Commit this?") counts as a question
            if x.get("type") == "tool_use" and x.get("name", "").endswith("wrap_up") \
                    and (x.get("input") or {}).get("ask") and not question:
                question = x["input"]["ask"]
    return short(text), short(question.replace("**", "").replace("`", "").strip(" *_#>"))


def hook():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    event = data.get("hook_event_name", "")
    sid = data.get("session_id") or "default"
    if event == "Stop":
        text, question = final_turn(data)
        if question:  # it stopped to ask you something -> red
            if set_status(sid, NEEDS, data, question):
                spawn("_flash", "needs")
        elif set_status(sid, DONE, data, text or "Done"):
            spawn("_flash", "done")
    elif event == "PreToolUse" and data.get("tool_name") == "AskUserQuestion":
        qs = (data.get("tool_input") or {}).get("questions") or [{}]
        if set_status(sid, NEEDS, data, qs[0].get("question") or "Asked you a question"):
            spawn("_flash", "needs")
    elif event == "Notification":
        kind = data.get("notification_type") or ""
        msg = data.get("message") or ""
        if kind in ("permission_prompt", "elicitation_dialog") or (not kind and "permission" in msg.lower()):
            token = set_status(sid, NEEDS, data, msg or "Needs your answer")
            if token:
                spawn("_flash", "needs")
                spawn("_watch", sid, token)
    elif event in ("UserPromptSubmit", "PostToolUse"):
        set_status(sid, WORKING, data, "Working…")
    elif event == "SessionEnd":
        clear(sid)


def web():
    """Chrome native-messaging host: the claude.ai extension reports working / done / end."""
    import struct
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return
    msg = json.loads(sys.stdin.buffer.read(struct.unpack("=I", raw)[0]))
    sid = str(msg.get("id") or "web")
    event = msg.get("event")
    url = str(msg.get("url") or "")
    info = {"title": short(msg.get("title") or "claude.ai chat", 60),
            "link": url if url.startswith("https://claude.ai/") else "",
            "app": "com.google.Chrome"}
    if event == "working":
        set_status(sid, WORKING, {}, "Working…", {**info, "need": "Working…"})
    elif event == "done":
        question = short(msg.get("question") or "")
        if question:  # Claude ended by asking you something -> red
            if set_status(sid, NEEDS, {}, question, {**info, "need": question}):
                spawn("_flash", "needs")
        elif set_status(sid, DONE, {}, "Done", {**info, "need": "claude.ai"}):
            spawn("_flash", "done")
    elif event == "end":
        clear(sid)
    out = json.dumps({"ok": True}).encode()
    sys.stdout.buffer.write(struct.pack("=I", len(out)) + out)
    sys.stdout.buffer.flush()


# ---------- setup / commands ----------

def setup():
    print("govee-claude setup\n")
    print("Paste your Govee API key (it won't show while you paste), then press Enter.")
    key = getpass.getpass("API key: ").strip()
    c = cfg()
    c["api_key"] = key
    try:
        devices = call(c, "GET", "/user/devices").get("data", [])
    except urllib.error.HTTPError as e:
        sys.exit(f"Govee said {e.code}. Double-check the key (it's in the email from Govee).")
    lights = [d for d in devices
              if any(x.get("instance") == "colorRgb" for x in d.get("capabilities", []))]
    if not lights:
        sys.exit("No color lights on this Govee account. Is the lamp added in the Govee Home app?")
    for i, d in enumerate(lights, 1):
        print(f"  {i}. {d.get('deviceName')}  ({d['sku']})")
    pick = 1
    if len(lights) > 1:
        pick = int(input(f"Which one is the floor lamp? [1-{len(lights)}]: ").strip() or 1)
    d = lights[pick - 1]
    conf = load(CONFIG, {})
    conf.update(api_key=key, sku=d["sku"], device=d["device"], name=d.get("deviceName"))
    save(CONFIG, conf, private=True)
    print(f"\nSaved. Using '{d.get('deviceName')}'. Make sure it's ON, watch it now...")
    demo()
    print("\nIf the lamp swept green, then red, then went back to your color, you're done.")


def demo():
    c = cfg()
    s = lamp_state(c)
    if s and not s["online"]:
        print("Govee says the lamp is OFFLINE (not on Wi-Fi). Phone-over-Bluetooth doesn't count.\n"
              "Fix: Govee Home app -> the lamp -> gear icon -> Wi-Fi -> reconnect (2.4 GHz network).\n"
              "Then run: python3 ~/govee-claude/govee_claude.py test")
        return
    if not s or not s["on"]:
        print("Lamp is off (or unreachable), so it stays quiet. Turn it on and run: govee-claude test")
        return
    flash("done")
    time.sleep(1.5)
    flash("needs")


def hook_cmd():
    py = shutil.which("python3") or sys.executable
    return f'"{py}" "{SCRIPT}" hook 2>/dev/null || true'


HOOK_EVENTS = {
    "Stop": None,
    "Notification": "permission_prompt|elicitation_dialog",
    "UserPromptSubmit": None,
    "PostToolUse": "*",
    "PreToolUse": "AskUserQuestion",
    "SessionEnd": None,
}


def install():
    settings = load(SETTINGS, {})
    if os.path.exists(SETTINGS):
        shutil.copy(SETTINGS, SETTINGS + ".bak-govee")
    uninstall(settings, write=False)
    hooks = settings.setdefault("hooks", {})
    for event, matcher in HOOK_EVENTS.items():
        entry = {"hooks": [{"type": "command", "command": hook_cmd(), "async": True, "timeout": 30}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        hooks.setdefault(event, []).append(entry)
    save(SETTINGS, settings)
    print(f"Hooks added to {SETTINGS} (backup: settings.json.bak-govee). New Claude chats pick them up.")


def uninstall(settings=None, write=True):
    settings = settings if settings is not None else load(SETTINGS, {})
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [e for e in hooks[event]
                        if not any("govee_claude.py" in h.get("command", "") for h in e.get("hooks", []))]
        if not hooks[event]:
            del hooks[event]
    if write:
        save(SETTINGS, settings)
        print("govee-claude hooks removed.")


# fake lamp so the logic can be tested without hardware (GOVEE_FAKE=1)
def fake_call(path, body):
    lamp = load(FAKE, {"powerSwitch": 1, "colorRgb": 0, "colorTemperatureK": 2700, "brightness": 40})
    if path == "/user/devices":
        return {"data": [{"sku": "H0000", "device": "FA:KE", "deviceName": "Fake Lamp",
                          "capabilities": [{"instance": "colorRgb"}]}]}
    if path == "/device/state":
        return {"payload": {"capabilities": [
            {"instance": "online", "state": {"value": True}}] + [
            {"instance": k, "state": {"value": v}} for k, v in lamp.items()]}}
    cap = body["payload"]["capability"]
    lamp[cap["instance"]] = cap["value"]
    if cap["instance"] == "colorRgb":
        lamp["colorTemperatureK"] = 0
    if cap["instance"] == "colorTemperatureK":
        lamp["colorRgb"] = 0
    save(FAKE, lamp)
    log(f"FAKE set {cap['instance']}={cap['value']}")
    return {"code": 200}


def status():
    c = cfg()
    st = load(STATE, new_state())
    print(f"lamp:     {c.get('name') or 'not set up (run: govee-claude setup)'}")
    if c.get("device"):
        s = lamp_state(c)
        print("online:   " + ("unknown" if not s else "yes, " + ("on" if s["on"] else "off")
                              if not s or s["online"] else "NO - lamp isn't on Wi-Fi (fix in Govee Home app)"))
    print(f"locked:   {bool(c.get('locked'))}")
    print(f"paused:   {os.path.exists(PAUSED)}")
    agents = st.get("agents", {}).values()
    for name in (NEEDS, DONE, WORKING):
        print(f"{name + ':':9} {sum(a.get('status') == name for a in agents)} agent(s)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "hook":
        try:
            hook()
        except Exception as e:  # never break Claude
            log(f"hook error: {e!r}")
    elif cmd == "_watch":
        try:
            watch(sys.argv[2], sys.argv[3])
        except Exception as e:
            log(f"watch error: {e!r}")
    elif cmd == "_flash":
        try:
            flash(sys.argv[2])
        except Exception as e:
            log(f"flash error: {e!r}")
    elif cmd == "setup":
        setup()
    elif cmd == "test":
        demo()
    elif cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    elif cmd == "clear":
        clear()
        print("Menu bar cleared.")
    elif cmd == "pause":
        open(PAUSED, "w").close()
        clear()
        print("Paused. Nothing reacts until: govee-claude resume")
    elif cmd == "resume":
        if os.path.exists(PAUSED):
            os.remove(PAUSED)
        print("Back on.")
    elif cmd == "web":
        try:
            web()
        except Exception as e:
            log(f"web error: {e!r}")
    elif cmd == "color":
        set_color(" ".join(sys.argv[2:]))
    elif cmd == "lock":
        lock(True)
    elif cmd == "unlock":
        lock(False)
    elif cmd == "sweep":  # just the "Claude's done" animation
        flash("done")
    elif cmd == "status":
        status()
    else:
        print(__doc__)
        print("commands: setup | test | sweep | color <name> [brightness] | lock | unlock | clear | pause | resume | status | install | uninstall")


if __name__ == "__main__":
    main()
