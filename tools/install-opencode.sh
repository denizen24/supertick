#!/usr/bin/env bash
# install-opencode.sh — поставить supertick в OpenCode без интерактива (121-567, H3).
#
#   tools/install-opencode.sh [--dir <каталог конфига>] [--project <репозиторий>] [--uninstall]
#
# Плагинов-наборов у OpenCode нет: навыки и агенты — файлы в каталоге конфига. Скрипт
#   1. собирает набор `tools/build-host.py opencode` (build/opencode, не в git);
#   2. кладёт в <каталог>/skills/ навыки supertick-team-feature (весь набор лежит внутри него: каталоги
#      навыков OpenCode открыты для чтения ведущему и исполнителям) и supertick-setup, в <каталог>/agents/ —
#      агентов ролей supertick-<роль>.md. Чужие файлы не трогает; повтор заменяет только свои.
# <каталог> по умолчанию $OPENCODE_CONFIG_DIR или ~/.config/opencode; --project <репозиторий> — <репозиторий>/.opencode.
# Навыки и агенты OpenCode видит в новой сессии (`opencode debug skill`, `opencode agent list`).
set -eu
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dir="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}"; uninstall=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) dir="$2"; shift 2 ;;
    --project) dir="$2/.opencode"; shift 2 ;;
    --uninstall) uninstall=1; shift ;;
    *) echo "install-opencode: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done

remove_ours() {
  rm -rf "$dir/skills/supertick-team-feature" "$dir/skills/supertick-setup"
  rm -f "$dir"/agents/supertick-*.md
}

if [ "$uninstall" = 1 ]; then
  remove_ours
  echo "удалён: supertick из OpenCode ($dir)"
  exit 0
fi

python3 "$here/tools/build-host.py" opencode >/dev/null
src="$here/build/opencode"
mkdir -p "$dir/skills" "$dir/agents"
for s in supertick-team-feature supertick-setup; do   # замена через соседний каталог: набор не бывает наполовину старым
  rm -rf "$dir/skills/$s.new"; cp -a "$src/skills/$s" "$dir/skills/$s.new"
  rm -rf "$dir/skills/$s"; mv "$dir/skills/$s.new" "$dir/skills/$s"
done
rm -f "$dir"/agents/supertick-*.md
cp "$src"/agents/supertick-*.md "$dir/agents/"
echo "установлен: supertick, $dir (навыки supertick-team-feature, supertick-setup; агентов $(ls "$dir"/agents/supertick-*.md | wc -l))"
