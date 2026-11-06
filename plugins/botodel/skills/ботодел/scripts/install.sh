#!/bin/bash
# Ботодел: копирует панель в ~/.bots-panel (macOS не даёт фоновым службам читать Рабочий стол
# и Документы) и ставит автозапуск. После правок bots.json или кода — запустить ещё раз.
#   ./install.sh /путь/к/bots.json
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"; DST="${BOTS_PANEL_HOME:-$HOME/.bots-panel}"
CFG="${1:-$DST/bots.json}"
[ -f "$CFG" ] || { echo "Нет bots.json: скопируй bots.example.json, впиши своих ботов и передай путь"; exit 1; }
mkdir -p "$DST"; cp "$SRC/panel.py" "$SRC/panel.html" "$SRC/open.sh" "$DST/"; chmod +x "$DST/open.sh"
[ "$CFG" -ef "$DST/bots.json" ] || cp "$CFG" "$DST/bots.json"
/usr/bin/python3 "$DST/panel.py" --dump > /dev/null || { echo "panel.py --dump упал: проверь bots.json"; exit 1; }
L="com.$(whoami).bots-panel"; P="$HOME/Library/LaunchAgents/$L.plist"
cat > "$P" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>Label</key><string>$L</string>
 <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>-u</string><string>$DST/panel.py</string></array>
 <key>EnvironmentVariables</key><dict><key>BOTS_PANEL_HOME</key><string>$DST</string></dict>
 <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer>
 <key>StandardOutPath</key><string>$DST/panel.log</string><key>StandardErrorPath</key><string>$DST/panel.log</string>
</dict></plist>
PL
pkill -f "$DST/panel.py" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$P"
sleep 2; curl -s -o /dev/null --max-time 3 http://127.0.0.1:$(python3 -c "import json;print(json.load(open('$DST/bots.json')).get('port',8795))")/ \
  && echo "Ботодел запущен. Ярлык: $SRC/make_app.sh" || echo "Не отвечает — смотри $DST/panel.log"
