#!/bin/bash
# Install or update FamilyBrief into ~/FamilyBrief/app.
# Safe to re-run: never touches the config, tokens or Keychain. It only adds what it creates to
# setup's record (~/.family/install-record.json), so `family-brief uninstall` can remove it.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${FAMILY_BRIEF_HOME:-$HOME/FamilyBrief}"
APP="$TARGET/app"

pick_python() {
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3.13 python3.12 python3.11 python3; do
    p=$(command -v "$c" 2>/dev/null) || continue
    if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

if [ "$(uname)" != "Darwin" ]; then echo "❌ Only macOS is supported for now"; exit 1; fi
PY=$(pick_python) || { echo "❌ Python 3.11 or later is needed. Install it with: brew install python"; exit 1; }

mkdir -p "$APP" "$TARGET/logs"
# The archive holds every collected message and the logs, so only this Mac account may read them
# (home folders let every standard account in). This also fixes installs from before it was done.
chmod 700 "$TARGET"
chmod -R go-rwx "$TARGET"
mkdir -p "$HOME/.family" && chmod 700 "$HOME/.family"
rsync -a --delete --exclude .venv --exclude '__pycache__' --exclude '*.egg-info' --exclude /tests "$PLUGIN_ROOT/app/" "$APP/"
VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$PLUGIN_ROOT/.claude-plugin/plugin.json" | head -1)
echo "${VERSION:-unknown}" > "$APP/VERSION"

[ -x "$APP/.venv/bin/python" ] || "$PY" -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1
"$APP/.venv/bin/pip" install -q -e "$APP"
RECORD=("$APP/.venv/bin/python" -m family_brief.install_record)
"${RECORD[@]}" program "$APP" logs "$TARGET/logs"
# The copy of the plugin a Codex install runs from; Claude Code manages its own copy.
if [ "$PLUGIN_ROOT" = "$TARGET/plugin" ]; then "${RECORD[@]}" plugin "$PLUGIN_ROOT"; fi

# --codex: also install the two skills for Codex, which reads user skills from ~/.agents/skills.
# They get unique names, Codex's $skill syntax, and a note saying where this plugin folder is.
if [ "${1:-}" = "--codex" ]; then
  "$APP/.venv/bin/python" - "$PLUGIN_ROOT" <<'PY'
import re, shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
skills = Path.home() / ".agents" / "skills"
for name in ("setup", "manage"):
    shutil.rmtree(skills / f"family-brief-{name}", ignore_errors=True)  # their names before 0.4.0
    text = (root / "skills" / name / "SKILL.md").read_text()
    text = text.replace(f"\nname: {name}\n", f"\nname: parent-recap-{name}\n", 1)
    text = re.sub(r"/parent-recap:(setup|manage)", r"$parent-recap-\1", text)
    head, sep, body = text.partition("\n# ")
    title, _, rest = body.partition("\n")
    note = f"\n\n> This skill is installed in Codex; PLUGIN (the plugin root folder) is `{root}`.\n"
    out = skills / f"parent-recap-{name}" / "SKILL.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(head + sep + title + note + rest)
    print(f"✅ Codex skill installed: ${out.parent.name}")
PY
  "${RECORD[@]}" codex-skill "$HOME/.agents/skills/parent-recap-setup" \
    codex-skill "$HOME/.agents/skills/parent-recap-manage"
fi

REAL_PY=$("$APP/.venv/bin/python" -c 'import os, sys; print(os.path.realpath(sys.executable))')
echo "✅ FamilyBrief ${VERSION:-} installed to $APP"
echo "   Command: $APP/.venv/bin/family-brief"
echo "   Real Python path (needed for the WhatsApp permission): $REAL_PY"
