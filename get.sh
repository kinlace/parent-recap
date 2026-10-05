#!/bin/bash
# Install or update Parent Recap from its latest stable release: the one line a family pastes
# into Terminal (README, "Install"), the same for every Household. It is fetched from the
# `stable` branch, as is everything it downloads, so a family only ever gets a tested release
# (ADR 0002):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/kinlace/parent-recap/stable/get.sh)"
#
# Without a flag: downloads the `stable` branch into ~/FamilyBrief/plugin, replacing the copy
#   there, runs its `install.sh`, and opens the setup page (ADR 0006), which stays served from
#   this Terminal window. Once the family picks Claude or ChatGPT there, the page installs the
#   Claude Code plugin or the Codex skills, so changes later work in the chat.
# The chat setup's two flags, for a family that asks for it:
# --claude: adds the `kinlace/parent-recap#stable` marketplace and installs the plugin for this
#   Mac user (or updates both), then starts Claude Code with setup. A `kinlace` marketplace from
#   somewhere else (the folder a release zip was unzipped into) is switched to that one.
# --codex: downloads the `stable` branch into ~/FamilyBrief/plugin, as without a flag, and runs
#   its `install.sh --codex`.
# Safe to run again: that is how an install updates, and the page resumes where setup got to.
set -euo pipefail

REPO="kinlace/parent-recap"
TARGET="${FAMILY_BRIEF_HOME:-$HOME/FamilyBrief}"
PLUGIN="$TARGET/plugin"

usage() {
  echo "Paste this into Terminal:"
  echo "  bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/$REPO/stable/get.sh)\""
  exit 1
}

# Whether `claude <args>` (a list in JSON) has an entry whose `key` is `value`.
# Captured first: `grep -q` stopping early could make `claude` fail with SIGPIPE under pipefail.
listed() {
  local key=$1 value=$2 list; shift 2
  list=$(claude "$@")
  grep -q "\"$key\": *\"$value\"" <<<"$list"
}

# The `kinlace` entry of `claude plugin marketplace list --json` on one line, or nothing.
kinlace_marketplace() {
  local list
  list=$(claude plugin marketplace list --json)
  tr -d '\n' <<<"$list" | tr '}' '\n' | grep -E '"name": *"kinlace"' || true
}

# The folder a marketplace entry from kinlace_marketplace comes from, for saying what was switched.
marketplace_folder() {
  local path
  path=$(sed -nE 's/.*"path": *"([^"]*)".*/\1/p' <<<"$1")
  echo "${path:-its old source}"
}

# Adds `<kind>: [<value>]` to setup's record, ~/.family/install-record.json, so uninstall
# removes what this installed. The program isn't installed yet to do it, so this writes the
# file the way family_brief.install_record does: owner-only, `{` on a line of its own.
record() {
  local file="$HOME/.family/install-record.json" entry="\"$1\": [\"$2\"]"
  mkdir -p -m 700 "$HOME/.family"
  if [ ! -s "$file" ] || [ "$(cat "$file")" = "{}" ]; then
    (umask 077; printf '{\n  %s\n}\n' "$entry" > "$file")
  elif ! grep -q "\"$1\":" "$file"; then
    sed -i '' "1s/^{\$/{\\
  $entry,/" "$file"
  fi
}

# The setup page installs the plugin the same way, in app/src/family_brief/chat_install.py:
# change both together.
install_claude() {
  command -v claude >/dev/null || {
    echo "❌ Claude Code isn't installed (there is no \`claude\` command)."
    echo "   Install it with: curl -fsSL https://claude.ai/install.sh | bash"
    echo "   Then open a new Terminal window (⌘N) and paste the line again."
    exit 1
  }
  # The plugin's name before 0.4.0. Settings and Briefs in ~/.family and ~/FamilyBrief stay.
  if listed id family-brief@family-brief plugin list --json; then
    claude plugin uninstall family-brief@family-brief
  fi
  if listed name family-brief plugin marketplace list --json; then
    claude plugin marketplace remove family-brief
  fi
  local marketplace switched="" installed=no new=()
  marketplace=$(kinlace_marketplace)
  if listed id parent-recap@kinlace plugin list --json; then installed=yes; fi
  # Only what wasn't there in any form before goes in the record: uninstall leaves the rest.
  [ "$installed" = yes ] || new+=(claude-plugin parent-recap@kinlace)
  [ -n "$marketplace" ] || new+=(claude-marketplace kinlace)
  # Installed from the release zip, `kinlace` comes from the unzipped folder, so updating it
  # would only reread that folder. ~/FamilyBrief/plugin stays: the Codex path uses it.
  if [ -n "$marketplace" ] && ! grep -qE "\"repo\": *\"$REPO(#[^\"]*)?\"" <<<"$marketplace"; then
    switched=$(marketplace_folder "$marketplace")
    if [ "$installed" = yes ]; then claude plugin uninstall parent-recap@kinlace; fi
    claude plugin marketplace remove kinlace
    marketplace="" installed=no
  fi
  if [ -n "$marketplace" ]; then
    claude plugin marketplace update kinlace
  else
    claude plugin marketplace add "$REPO#stable"
  fi
  if [ "$installed" = yes ]; then
    claude plugin update parent-recap@kinlace
  else
    # User scope, so the plugin isn't tied to the folder this Terminal is in.
    claude plugin install parent-recap@kinlace --scope user
  fi
  set -- "${new[@]+"${new[@]}"}"
  while [ $# -gt 0 ]; do record "$1" "$2"; shift 2; done
  [ -z "$switched" ] || echo "Switched Parent Recap from $switched to $REPO#stable on GitHub."
  echo "✅ Parent Recap is installed in Claude Code. Starting setup…"
  exec claude "/parent-recap:setup"
}

is_parent_recap() {
  grep -qE '"name": *"(parent-recap|family-brief)"' "$1/.claude-plugin/plugin.json" 2>/dev/null
}

# Puts the `stable` branch in ~/FamilyBrief/plugin, replacing what's there only if it's Parent Recap.
download_stable() {
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
  rm -rf "$download"  # now, since opening the page with `exec` skips the EXIT trap
}

install_page() {
  download_stable
  bash "$PLUGIN/install.sh"
  echo "✅ Parent Recap is installed. The setup page opens in your browser now."
  echo "   Keep this Terminal window open until setup is done: the page runs from it."
  exec "$TARGET/app/.venv/bin/family-brief" setup page
}

install_codex() {
  download_stable
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
  "") install_page ;;
  *) usage ;;
esac
