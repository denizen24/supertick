#!/usr/bin/env bash
# install-codex.sh — поставить плагин supertick в Codex CLI без интерактива (121-567, H4).
#
#   tools/install-codex.sh [--codex-home <каталог>] [--market <каталог>] [--uninstall]
#
# Codex ставит плагины только из маркетплейса (проверено 13.11.2026 на 0.167.2):
#   1. собирает набор `tools/build-host.py codex` (build/codex/supertick, не в git);
#   2. кладёт его в локальный маркетплейс <market>/plugins/supertick и пишет
#      <market>/.agents/plugins/marketplace.json (имя маркетплейса — supertick-local);
#   3. `codex plugin marketplace add <market>` (повтор безвреден) и `codex plugin add supertick@supertick-local` —
#      Codex копирует набор в <codex-home>/plugins/cache/supertick-local/supertick/<версия>/ (повтор обновляет).
# <codex-home> по умолчанию $CODEX_HOME или ~/.codex, <market> — ~/.local/share/supertick/codex-market.
# Навыки supertick:team-feature и supertick:setup Codex видит в новой сессии.
set -eu
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
market="$HOME/.local/share/supertick/codex-market"; uninstall=0
while [ $# -gt 0 ]; do
  case "$1" in
    --codex-home) export CODEX_HOME="$2"; shift 2 ;;
    --market) market="$2"; shift 2 ;;
    --uninstall) uninstall=1; shift ;;
    *) echo "install-codex: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done
command -v codex >/dev/null || { echo "install-codex: codex не найден в PATH" >&2; exit 2; }
name=supertick-local
quiet() { grep -v '^WARNING: proceeding, even though we could not create PATH aliases' || true; }

if [ "$uninstall" = 1 ]; then
  codex plugin remove "supertick@$name" 2>&1 | quiet || true
  codex plugin marketplace remove "$name" 2>&1 | quiet || true
  rm -rf "$market"
  echo "удалён: supertick из Codex (${CODEX_HOME:-$HOME/.codex})"
  exit 0
fi

python3 "$here/tools/build-host.py" codex
mkdir -p "$market/.agents/plugins" "$market/plugins"
rm -rf "$market/plugins/supertick.new"; cp -a "$here/build/codex/supertick" "$market/plugins/supertick.new"
rm -rf "$market/plugins/supertick"; mv "$market/plugins/supertick.new" "$market/plugins/supertick"
cat > "$market/.agents/plugins/marketplace.json.tmp" <<EOF
{
  "name": "$name",
  "interface": {"displayName": "supertick (локальная сборка)"},
  "plugins": [
    {"name": "supertick", "source": {"source": "local", "path": "./plugins/supertick"},
     "policy": {"installation": "AVAILABLE"}, "category": "Coding"}
  ]
}
EOF
mv "$market/.agents/plugins/marketplace.json.tmp" "$market/.agents/plugins/marketplace.json"
codex plugin marketplace add "$market" 2>&1 | quiet
out=$(codex plugin add "supertick@$name" 2>&1 | quiet)
echo "$out"
root=$(printf '%s\n' "$out" | sed -n 's/^Installed plugin root: //p')
[ -n "$root" ] && [ -f "$root/LEAD.md" ] || { echo "install-codex: Codex не сообщил корень плагина или в нём нет LEAD.md" >&2; exit 1; }
[ -x "$root/scripts/supervisor-wait.sh" ] || { echo "install-codex: в копии Codex потеряно право на исполнение скриптов" >&2; exit 1; }
echo "установлен: supertick, $root"
echo "ведущий: codex --sandbox danger-full-access, затем навык supertick:team-feature"
