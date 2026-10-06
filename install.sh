#!/bin/bash
# Install or update Parent Recap into ~/ParentRecap/app.
# Safe to re-run: never touches the config, tokens or Keychain. It only adds what it creates to
# setup's record (~/.family/install-record.json), so `parent-recap uninstall` can remove it.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${PARENT_RECAP_HOME:-$HOME/ParentRecap}"
APP="$TARGET/app"

# ── Old install check
# An install from before ADR 0010 lives in ~/FamilyBrief and runs the jobs com.family.brief and
# com.family.weekend-events. There is no migration: the version that made it uninstalls it. A
# ~/FamilyBrief that only holds a kept archive of past Briefs is not an install.
# Kept in sync with get.sh: that one runs before anything is downloaded, this one when a
# release zip's install.sh runs alone.
refuse_old_install() {
  local old=no label plist
  [ ! -d "$HOME/FamilyBrief/app" ] || old=yes
  for label in com.family.brief com.family.weekend-events; do
    plist="$HOME/Library/LaunchAgents/$label.plist"
    if [ -f "$plist" ] && grep -q family_brief "$plist"; then old=yes; fi
  done
  [ "$old" = yes ] || return 0
  echo "❌ An older Parent Recap is installed (its folder is ~/FamilyBrief). Nothing was changed."
  echo "   Uninstall it with its own version first, then paste the install line again:"
  echo "     ~/FamilyBrief/app/.venv/bin/family-brief uninstall"
  exit 1
}

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
refuse_old_install
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

# pip's full output goes to a dated log, not the Terminal: a failed build is pages of compiler
# output a family can't act on. The log starts with the one line triage needs about this Mac,
# which writes the home folder as ~ so it names no account.
LOG="$TARGET/logs/install-$(date +%Y-%m-%d-%H%M%S).log"
pip_failed() {
  echo "❌ Parent Recap couldn't install its Python packages. The full log is at $LOG"
  echo "   Run the install again. If it fails again, send that log to the Parent Recap team."
  exit 1
}
# Prebuilt packages only: building one from source can download a compiler and still fail, and
# then pip picks the newest version with a ready-made package for this Mac. Pinned to what was
# tested (app/constraints.txt, see CONTRIBUTING.md), not to what was published this morning.
PIP=("$APP/.venv/bin/pip" install --only-binary :all:)
# pip upgrades itself first, so the line names the pip that installs the packages.
UPGRADE=$("${PIP[@]}" --upgrade pip 2>&1) && UPGRADED=yes || UPGRADED=no
tilde() {
  case "$1" in
    "$HOME"/*) [ "${HOME:-/}" != / ] && echo "~/${1:${#HOME}+1}" || echo "$1" ;;
    *) echo "$1" ;;
  esac
}
CC=$(command -v cc 2>/dev/null) || CC=none
MAC="Mac: $(uname -m), macOS $(sw_vers -productVersion 2>/dev/null || echo unknown),"
MAC+=" Python $(tilde "$PY") ($("$PY" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)),"
MAC+=" pip $("$APP/.venv/bin/python" -c 'from importlib.metadata import version; print(version("pip"))' 2>/dev/null || echo unknown), cc $(tilde "$CC")"
printf '%s\n%s\n' "$MAC" "$UPGRADE" > "$LOG"
[ "$UPGRADED" = yes ] || pip_failed
"${PIP[@]}" -c "$APP/constraints.txt" -e "$APP" >> "$LOG" 2>&1 || pip_failed
# Google's packages only for a Household whose config uses Google Calendar: turning it on in
# manage runs this script again, and an update keeps them.
if "$APP/.venv/bin/python" -m family_brief.google_packages wanted; then
  "${PIP[@]}" -c "$APP/constraints.txt" -e "$APP[google]" >> "$LOG" 2>&1 || pip_failed
fi
RECORD=("$APP/.venv/bin/python" -m family_brief.install_record)
"${RECORD[@]}" program "$APP" logs "$TARGET/logs"
# The copy of the plugin the install line downloads, which the Codex skills run from; Claude
# Code manages its own copy.
if [ "$PLUGIN_ROOT" = "$TARGET/plugin" ]; then "${RECORD[@]}" plugin "$PLUGIN_ROOT"; fi

# --codex: also install the two skills for Codex, which reads user skills from ~/.agents/skills.
# They get unique names, Codex's $skill syntax, and a note saying where this plugin folder is.
# Without it, the setup page installs them once the family picks ChatGPT.
if [ "${1:-}" = "--codex" ]; then
  "$APP/.venv/bin/python" -m family_brief.chat_install codex-skills "$PLUGIN_ROOT"
fi

REAL_PY=$("$APP/.venv/bin/python" -c 'import os, sys; print(os.path.realpath(sys.executable))')
echo "✅ Parent Recap ${VERSION:-} installed to $APP"
echo "   Command: $APP/.venv/bin/parent-recap"
echo "   Real Python path (needed for the WhatsApp permission): $REAL_PY"
