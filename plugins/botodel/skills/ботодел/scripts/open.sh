#!/bin/bash
# Ярлык «Ботодел»: поднять панель, если не запущена, и открыть отдельным окном.
DIR="${BOTS_PANEL_HOME:-$HOME/.bots-panel}"; U=http://127.0.0.1:8795/
if ! curl -s -o /dev/null --max-time 2 "$U"; then
  nohup /usr/bin/python3 -u "$DIR/panel.py" >> "$HOME/.business/growth/bots-panel.log" 2>&1 &
  for i in $(seq 1 20); do curl -s -o /dev/null --max-time 1 "$U" && break; sleep 0.3; done
fi
C="/Applications/Google Chrome.app"
if [ -d "$C" ]; then open -na "$C" --args --app="$U" --window-size=1320,900; else open "$U"; fi
