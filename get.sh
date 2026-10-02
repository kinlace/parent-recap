#!/bin/bash
# Install or update Parent Recap from its latest stable release: the one line a family pastes
# into Terminal (README, "Install"). It is fetched from the `stable` branch, as is everything
# it downloads, so a family only ever gets a tested release (ADR 0002):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/kinlace/parent-recap/stable/get.sh)" - --claude
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/kinlace/parent-recap/stable/get.sh)" - --codex
#
# --claude: adds the `kinlace/parent-recap#stable` marketplace and installs the plugin for this
#   Mac user (or updates both), then starts Claude Code with setup.
# --codex: downloads the `stable` branch into ~/FamilyBrief/plugin, replacing the copy there,
#   and runs its `install.sh --codex`.
# Safe to run again: that is how a Codex install updates.
set -euo pipefail

REPO="kinlace/parent-recap"
TARGET="${FAMILY_BRIEF_HOME:-$HOME/FamilyBrief}"
PLUGIN="$TARGET/plugin"

usage() {
  echo "Paste one of these into Terminal:"
  echo "  Claude Code: bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/$REPO/stable/get.sh)\" - --claude"
  echo "  Codex:       bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/$REPO/stable/get.sh)\" - --codex"
  exit 1
}

# Whether `claude <args>` (a list in JSON) has an entry whose `key` is `value`.
# Captured first: `grep -q` stopping early could make `claude` fail with SIGPIPE under pipefail.
listed() {
  local key=$1 value=$2 list; shift 2
  list=$(claude "$@")
  grep -q "\"$key\": *\"$value\"" <<<"$list"
}

install_claude() {
  command -v claude >/dev/null || {
    echo "❌ Claude Code isn't installed (there is no \`claude\` command)."
    echo "   Install it (https://claude.com/claude-code), open a new Terminal window (⌘N) and paste the line again."
    exit 1
  }
  # The plugin's name before 0.4.0. Settings and Briefs in ~/.family and ~/FamilyBrief stay.
  if listed id family-brief@family-brief plugin list --json; then
    claude plugin uninstall family-brief@family-brief
  fi
  if listed name family-brief plugin marketplace list --json; then
    claude plugin marketplace remove family-brief
  fi
  if listed name kinlace plugin marketplace list --json; then
    claude plugin marketplace update kinlace
  else
    claude plugin marketplace add "$REPO#stable"
  fi
  if listed id parent-recap@kinlace plugin list --json; then
    claude plugin update parent-recap@kinlace
  else
    # User scope, so the plugin isn't tied to the folder this Terminal is in.
    claude plugin install parent-recap@kinlace --scope user
  fi
  echo "✅ Parent Recap is installed in Claude Code. Starting setup…"
  exec claude "/parent-recap:setup"
}

is_parent_recap() {
  grep -qE '"name": *"(parent-recap|family-brief)"' "$1/.claude-plugin/plugin.json" 2>/dev/null
}

install_codex() {
  if [ -e "$PLUGIN" ] && ! is_parent_recap "$PLUGIN"; then
    echo "❌ $PLUGIN holds something that isn't Parent Recap, so it was left as it is."
    echo "   Move it somewhere else and paste the line again."
    exit 1
  fi
  mkdir -p "$TARGET"
  # Not local: the EXIT trap runs after this function has returned.
  download=$(mktemp -d "$TARGET/.plugin-download.XXXXXX")
  trap 'rm -rf "$download"' EXIT
  echo "Downloading the latest stable release of Parent Recap…"
  mkdir "$download/plugin"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/stable.tar.gz" \
    | tar -xz -C "$download/plugin" --strip-components 1
  is_parent_recap "$download/plugin" || { echo "❌ The download isn't Parent Recap; nothing was changed."; exit 1; }
  # Swap the new copy in whole, so a file the new release dropped doesn't stay behind.
  [ ! -e "$PLUGIN" ] || mv "$PLUGIN" "$download/old"
  mv "$download/plugin" "$PLUGIN" || { [ ! -e "$download/old" ] || mv "$download/old" "$PLUGIN"; exit 1; }

  bash "$PLUGIN/install.sh" --codex
  if [ -f "$HOME/.family/config.yaml" ]; then
    echo "✅ Parent Recap is updated. Open Codex, start a new chat and say \"upgrade Parent Recap\"."
  else
    echo "✅ Parent Recap is installed. Open Codex, start a new chat and type \$parent-recap-setup"
  fi
}

[ "$(uname)" = "Darwin" ] || { echo "❌ Parent Recap only runs on macOS for now."; exit 1; }
case "${1:-}" in
  --claude) install_claude ;;
  --codex) install_codex ;;
  *) usage ;;
esac
