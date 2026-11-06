#!/bin/bash
# Ярлык «Ботодел» в ~/Applications + в Док. Открывает панель отдельным окном Chrome (или браузером).
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"; DST="${BOTS_PANEL_HOME:-$HOME/.bots-panel}"; NAME="${1:-Ботодел}"
A="$HOME/Applications/$NAME.app"; mkdir -p "$HOME/Applications"; rm -rf "$A"
osacompile -o "$A" -e "do shell script \"BOTS_PANEL_HOME='$DST' '$DST/open.sh' > /dev/null 2>&1 &\""
cp "$SRC/icon.icns" "$A/Contents/Resources/applet.icns"; touch "$A"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$A"
if ! defaults read com.apple.dock persistent-apps | grep -q "$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$NAME.app")"; then
  URL=$(python3 -c "import pathlib,sys;print(pathlib.Path(sys.argv[1]).as_uri()+'/')" "$A")
  defaults write com.apple.dock persistent-apps -array-add "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$URL</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
  killall Dock
fi
echo "Готово: $A (и в Доке)"
