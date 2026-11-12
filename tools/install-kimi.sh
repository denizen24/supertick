#!/usr/bin/env bash
# install-kimi.sh — поставить плагин supertick в Kimi Code CLI без интерактива (121-567, H1).
#
#   tools/install-kimi.sh [--kimi-home <каталог>] [--uninstall]
#
# Делает то же, что `/plugins install <путь>` в интерактивной сессии Kimi (проверено спайком S1:
# плагин, оказавшийся в <kimi-home>/plugins/managed/<id>/ и в plugins/installed.json, работает):
#   1. собирает набор `tools/build-host.py kimi` (build/kimi/supertick, не в git);
#   2. копирует его в <kimi-home>/plugins/managed/supertick/ (прежняя копия заменяется целиком);
#   3. пишет запись `supertick` в plugins/installed.json, прежний файл — копией installed.json.bak-<время>.
# <kimi-home> по умолчанию ~/.kimi-code. Новые навыки Kimi видит после перезапуска сессии.
set -eu
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
kimi_home="$HOME/.kimi-code"; uninstall=0
while [ $# -gt 0 ]; do
  case "$1" in
    --kimi-home) kimi_home="$2"; shift 2 ;;
    --uninstall) uninstall=1; shift ;;
    *) echo "install-kimi: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done
plugins="$kimi_home/plugins"; dest="$plugins/managed/supertick"; reg="$plugins/installed.json"
mkdir -p "$plugins/managed"
[ -f "$reg" ] && cp -p "$reg" "$reg.bak-$(date +%Y%m%d-%H%M%S)"
if [ "$uninstall" = 1 ]; then
  rm -rf "$dest"
else
  python3 "$here/tools/build-host.py" kimi
  rm -rf "$dest.new"; cp -a "$here/build/kimi/supertick" "$dest.new"
  rm -rf "$dest"; mv "$dest.new" "$dest"
fi
python3 - "$reg" "$dest" "$uninstall" "$here/build/kimi/supertick" <<'PY'
import json, os, sys
from datetime import datetime, timezone
reg, dest, uninstall, src = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
data = {"version": 1, "plugins": []}
if os.path.isfile(reg):
    data = json.load(open(reg, encoding="utf-8"))
now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
old = next((p for p in data["plugins"] if p.get("id") == "supertick"), None)
data["plugins"] = [p for p in data["plugins"] if p.get("id") != "supertick"]
if not uninstall:
    data["plugins"].append({"id": "supertick", "root": dest, "source": "local-path", "origin": "custom",
                            "enabled": True, "installedAt": (old or {}).get("installedAt", now), "updatedAt": now,
                            "originalSource": src})
tmp = reg + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.replace(tmp, reg)
print(("удалён" if uninstall else "установлен") + f": supertick, {dest}")
PY
