#!/bin/bash
# Builds the menu bar guy, starts it at login, and adds the Claude Code hooks.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
PY="$(command -v python3)"
APP="$HOME/Applications/Claude Lamp.app"
AGENT="$HOME/Library/LaunchAgents/com.govee-claude.bar.plist"

echo "• building menu bar app"
mkdir -p "$APP/Contents/MacOS"
swiftc -O bar/main.swift -o "$APP/Contents/MacOS/ClaudeLamp"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.govee-claude.bar</string>
  <key>CFBundleName</key><string>Claude Lamp</string>
  <key>CFBundleExecutable</key><string>ClaudeLamp</string>
  <key>LSUIElement</key><true/>
</dict></plist>
EOF

echo "• starting it at login"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.govee-claude.bar</string>
  <key>ProgramArguments</key><array>
    <string>$APP/Contents/MacOS/ClaudeLamp</string>
    <string>$HERE/govee_claude.py</string>
    <string>$PY</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/com.govee-claude.bar" 2>/dev/null || true
for i in 1 2 3 4 5 6 7 8 9 10; do   # bootout finishes in the background; retry until it's gone
  launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null && break
  sleep 0.5
done

echo "• adding Claude Code hooks"
"$PY" govee_claude.py install

echo "• letting the Chrome extension talk to this script (claude.ai web chats)"
HOST="$HERE/web/host.sh"
printf '#!/bin/bash\nexec "%s" "%s/govee_claude.py" web\n' "$PY" "$HERE" > "$HOST"
chmod +x "$HOST"
NM="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
mkdir -p "$NM"
cat > "$NM/com.govee_claude.web.json" <<JSON
{
  "name": "com.govee_claude.web",
  "description": "Claude Lamp: claude.ai web chats",
  "path": "$HOST",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://cdidfmohkckgfkcdodohbiehbcfcdadh/"]
}
JSON
echo "  Then load the extension once: chrome://extensions -> Developer mode -> Load unpacked -> $HERE/web/extension"

echo
echo "Done. First time? Run: python3 govee_claude.py setup   (paste your Govee API key)"
