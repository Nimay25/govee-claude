#!/usr/bin/env python3
"""govee-claude: your Govee lamp tells you when Claude needs you.

  Claude finished          -> lamp goes warm gold
  Claude is blocked on you -> gold, then pulses red if you don't answer
  You reply                -> lamp goes back to whatever it was
  Lamp is off              -> nothing happens

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
    "red_after_sec": 90,       # unanswered permission prompt -> red after this long
    "gold_max_min": 20,        # give your vibe back after this long (0 = never)
    "min_brightness": 30,      # so gold is visible even if the lamp is dimmed way down
}

COLOR = "devices.capabilities.color_setting"
RANGE = "devices.capabilities.range"
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


# ---------- looks ----------

def show_gold(c, saved):
    set_rgb(c, c["gold"])
    if saved["brightness"] < c["min_brightness"]:
        set_brightness(c, c["min_brightness"])


def show_red(c, saved):
    hi = max(saved["brightness"], 70)
    set_rgb(c, c["red"])
    for _ in range(3):          # slow breathe, not a strobe
        set_brightness(c, 12)
        time.sleep(0.7)
        set_brightness(c, hi)
        time.sleep(0.7)


def restore(c, saved):
    now = lamp_state(c)
    if now and not now["on"]:
        return  # you turned it off meanwhile; don't turn it back on
    if saved["kelvin"]:
        control(c, COLOR, "colorTemperatureK", saved["kelvin"])
    else:
        set_rgb(c, saved["rgb"])
    set_brightness(c, saved["brightness"])


def render(c, st):
    """Make the lamp match the pending list (only if we took over the lamp)."""
    saved = st.get("saved")
    if not saved:
        return
    active = [p for p in st["pending"].values() if not p.get("quiet")]
    want = "vibe" if not active else ("red" if any(p["red"] for p in active) else "gold")
    if want == st.get("showing", "vibe"):
        return
    log(f"lamp -> {want}")
    if want == "vibe":
        restore(c, saved)
        st["saved"] = None
    elif want == "gold":
        show_gold(c, saved)
    else:
        show_red(c, saved)
    st["showing"] = want


def new_state():
    return {"pending": {}, "saved": None, "showing": "vibe"}


# ---------- the actual behaviour ----------

def attention(sid, urgent, info=None):
    """Record that a chat needs you (menu bar always sees it); light the lamp if it's on."""
    c = cfg()
    if os.path.exists(PAUSED):
        return
    with locked():
        st = load(STATE, new_state())
        if not st.get("saved") and c.get("device"):
            s = lamp_state(c)
            if s and s["on"] and s["online"]:
                st.update(saved=s, showing="vibe")
            else:
                log(f"lamp off/unreachable, menu bar only ({sid[:8]})")
        token = uuid.uuid4().hex
        st["pending"][sid] = {"token": token, "since": time.time(), "urgent": urgent,
                              "red": False, "quiet": False, **(info or {})}
        log(f"{'URGENT' if urgent else 'done'} from {sid[:8]}")
        render(c, st)
        save(STATE, st)
    spawn_watch(sid, token)


def clear(sid=None):
    """You replied (sid) or ran `clear` (None = everything)."""
    st = load(STATE, new_state())
    if sid is not None and sid not in st["pending"]:
        return  # fast path: nothing to do, no lock, no network
    c = cfg()
    with locked():
        st = load(STATE, new_state())
        if sid is None:
            st["pending"] = {}
        elif st["pending"].pop(sid, None) is None:
            return
        render(c, st)
        save(STATE, st)


def spawn_watch(sid, token):
    subprocess.Popen([sys.executable, SCRIPT, "_watch", sid, token],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True,
                     env=os.environ.copy())


def watch(sid, token):
    """Background: escalate to red, or hand the vibe back after a while."""
    c = cfg()
    while True:
        with locked():
            st = load(STATE, new_state())
            p = st["pending"].get(sid)
            if not p or p["token"] != token:
                return  # you already replied, or something newer took over
            age = time.time() - p["since"]
            if p["urgent"]:
                if age >= c["red_after_sec"]:
                    p["red"] = True
                    render(c, st)
                    save(STATE, st)
                    return  # red stays until you deal with it
                wait = c["red_after_sec"] - age
            else:
                limit = c["gold_max_min"] * 60
                if not limit:
                    return
                if age >= limit:
                    p["quiet"] = True  # lamp goes back to your vibe; menu bar keeps it
                    render(c, st)
                    save(STATE, st)
                    return
                wait = limit - age
        time.sleep(max(1, wait))


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


def last_text(data):
    """First bit of Claude's final message, from the transcript."""
    if data.get("last_assistant_message"):
        return short(data["last_assistant_message"])
    try:
        with open(data["transcript_path"], "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 300_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except (OSError, KeyError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") != "assistant":
            continue
        content = obj.get("message", {}).get("content", [])
        text = " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
        if text.strip():
            return short(text)
    return ""


def hook():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    event = data.get("hook_event_name", "")
    sid = data.get("session_id") or "default"
    if event == "Stop":
        attention(sid, urgent=False, info=chat_info(data, last_text(data) or "Done"))
    elif event == "Notification":
        kind = data.get("notification_type") or ""
        msg = data.get("message") or ""
        if kind in ("permission_prompt", "elicitation_dialog") or (not kind and "permission" in msg.lower()):
            attention(sid, urgent=True, info=chat_info(data, msg or "Needs your answer"))
    elif event in ("UserPromptSubmit", "PostToolUse", "SessionEnd"):
        clear(sid)


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
    print("\nIf the lamp went gold -> red -> back to normal, you're done.")


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
    with locked():
        show_gold(c, s)
        time.sleep(3)
        show_red(c, s)
        time.sleep(1.5)
        restore(c, s)


def hook_cmd():
    py = shutil.which("python3") or sys.executable
    return f'"{py}" "{SCRIPT}" hook 2>/dev/null || true'


HOOK_EVENTS = {
    "Stop": None,
    "Notification": "permission_prompt|elicitation_dialog",
    "UserPromptSubmit": None,
    "PostToolUse": "*",
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
    print(f"paused:   {os.path.exists(PAUSED)}")
    print(f"showing:  {st.get('showing', 'vibe')}")
    print(f"waiting:  {len(st['pending'])} chat(s)")


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
        print("Lamp back to normal.")
    elif cmd == "seen":  # menu bar click on one chat
        clear(sys.argv[2])
    elif cmd == "pause":
        open(PAUSED, "w").close()
        clear()
        print("Paused. Lamp won't react until: govee-claude resume")
    elif cmd == "resume":
        if os.path.exists(PAUSED):
            os.remove(PAUSED)
        print("Back on.")
    elif cmd == "status":
        status()
    else:
        print(__doc__)
        print("commands: setup | test | install | uninstall | clear | pause | resume | status")


if __name__ == "__main__":
    main()
