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

# ── Parent Recap's own Python (ADR 0011)
# A pinned python-build-standalone build, so no `brew upgrade` or `uv python upgrade` can move the
# Python that WhatsApp's permission names, or break the venv. To bump it, see CONTRIBUTING.md.
PYTHON_VERSION=3.13.16
PYTHON_BUILD=20261003
PYTHON_SHA256_AARCH64=d8975d7df4f08f7b1c7aafcdfacbddcec3d366415f2c1a72b2466b6850815933
PYTHON_SHA256_X86_64=8e9cb087305bfb8969f68a905f79f41469d4aa5220c1aa71ada7fc9953bdba0f
PYTHON_RELEASES=https://github.com/astral-sh/python-build-standalone/releases/download
# Each pinned version gets its own folder: macOS is assumed not to keep the permission for a
# binary replaced at the same path (#168).
RUNTIMES="$TARGET/runtime"
RUNTIME="$RUNTIMES/python-$PYTHON_VERSION-$PYTHON_BUILD"
PY="$RUNTIME/bin/python3"

# ── Parent Recap's own Node (ADR 0011)
# A nodejs.org LTS release, which the wilma CLI runs on (own_node.py), so a family needs no Node,
# and no Homebrew, for Wilma. To bump it, see CONTRIBUTING.md.
NODE_VERSION=24.21.0
NODE_SHA256_ARM64=6239d4cf92d864487ec8cd3615038f7b67e7f58b77b21cd2f09ea9fbd68065fe
NODE_SHA256_X64=0ae5a24c24bb7d015cd816c5036b3f90f2945aa872fcf54e58da054753b3a299
NODE_RELEASES=https://nodejs.org/dist
NODE_RUNTIME="$RUNTIMES/node-v$NODE_VERSION"
NODE="$NODE_RUNTIME/bin/node"

# `uname -m` says x86_64 in a Rosetta terminal; this says 1 on any Apple Silicon Mac.
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then ARCH=aarch64; else ARCH=x86_64; fi

runtime_failed() {
  echo "❌ $1 Nothing was changed."
  echo "   Run the install again. If it fails again, tell the Parent Recap team."
  exit 1
}

# Downloads the pinned $1 (Python or Node) from $2, checks it against the checksum $3, and unpacks
# it into $tmp/$1, where $4 must then be.
fetch_runtime() {
  local what=$1 url=$2 sha=$3 program=$4
  local tarball
  tarball="$tmp/$(basename "$url")"
  curl -fsSL --retry 2 -o "$tarball" "$url" 2>/dev/null ||
    runtime_failed "Parent Recap couldn't download its $what (is this Mac online?)."
  [ "$(shasum -a 256 "$tarball" | cut -d' ' -f1)" = "$sha" ] ||
    runtime_failed "The $what Parent Recap downloaded isn't the one it expects (its checksum doesn't match), so it wasn't used."
  mkdir "$tmp/$what"
  tar -xf "$tarball" -C "$tmp/$what" --strip-components 1 2>/dev/null && [ -x "$tmp/$what/$program" ] ||
    runtime_failed "Parent Recap couldn't unpack the $what it downloaded."
}

# Moves the $1 unpacked in $tmp to its folder $2: under a temporary name first, since $TMPDIR may
# be another disk, and only a rename on the same disk can't leave a half-copied folder behind. A
# run cut off here leaves .incoming, which the next one clears.
place_runtime() {
  rm -rf "$RUNTIMES/.incoming"
  { mv "$tmp/$1" "$RUNTIMES/.incoming" && mv "$RUNTIMES/.incoming" "$2"; } || {
    rm -rf "$RUNTIMES/.incoming"
    runtime_failed "Parent Recap couldn't put the $1 it downloaded in place (is the disk full?)."
  }
}

# Downloads and unpacks the pinned Python and Node unless they're already there. Both are
# downloaded and checked in a temporary folder before either is put in place, so a failure
# leaves the install as it was.
install_runtimes() {
  local python=no node=no
  [ -x "$PY" ] || python=yes
  [ -x "$NODE" ] || node=yes
  [ "$python$node" != nono ] || return 0
  local py_sha=$PYTHON_SHA256_X86_64 node_arch=x64 node_sha=$NODE_SHA256_X64
  if [ "$ARCH" = aarch64 ]; then
    py_sha=$PYTHON_SHA256_AARCH64 node_arch=arm64 node_sha=$NODE_SHA256_ARM64
  fi
  tmp=$(mktemp -d)  # not local: the trap reads it when runtime_failed exits
  trap 'rm -rf "$tmp"' EXIT
  [ "$python" = no ] || fetch_runtime Python \
    "$PYTHON_RELEASES/$PYTHON_BUILD/cpython-$PYTHON_VERSION+$PYTHON_BUILD-$ARCH-apple-darwin-install_only.tar.gz" \
    "$py_sha" bin/python3
  [ "$node" = no ] || fetch_runtime Node \
    "$NODE_RELEASES/v$NODE_VERSION/node-v$NODE_VERSION-darwin-$node_arch.tar.xz" "$node_sha" bin/node
  mkdir -p "$RUNTIMES"
  [ "$python" = no ] || place_runtime Python "$RUNTIME"
  [ "$node" = no ] || place_runtime Node "$NODE_RUNTIME"
  rm -rf "$tmp"
  trap - EXIT
}

# The venv's Python with its links followed, which is what WhatsApp's permission names; empty
# without a venv.
venv_real_python() {
  [ -e "$APP/.venv/bin/python" ] || [ -L "$APP/.venv/bin/python" ] || return 0
  "$PY" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$APP/.venv/bin/python"
}

if [ "$(uname)" != "Darwin" ]; then echo "❌ Only macOS is supported for now"; exit 1; fi
refuse_old_install
install_runtimes

mkdir -p "$APP" "$TARGET/logs"
# The archive holds every collected message and the logs, so only this Mac account may read them
# (home folders let every standard account in). This also fixes installs from before it was done.
chmod 700 "$TARGET"
chmod -R go-rwx "$TARGET"
mkdir -p "$HOME/.family" && chmod 700 "$HOME/.family"
rsync -a --delete --exclude .venv --exclude '__pycache__' --exclude '*.egg-info' --exclude /tests "$PLUGIN_ROOT/app/" "$APP/"
VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$PLUGIN_ROOT/.claude-plugin/plugin.json" | head -1)
echo "${VERSION:-unknown}" > "$APP/VERSION"

# A venv on another Python (Homebrew's, before ADR 0011) or an older pinned one is rebuilt.
OLD_REAL_PY=$(venv_real_python)
REAL_PY=$("$PY" -c 'import os, sys; print(os.path.realpath(sys.executable))')
if [ "$OLD_REAL_PY" != "$REAL_PY" ] || ! "$APP/.venv/bin/python" -c '' 2>/dev/null; then
  rm -rf "$APP/.venv"
  "$PY" -m venv "$APP/.venv"
fi

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
MAC="Mac: $ARCH, macOS $(sw_vers -productVersion 2>/dev/null || echo unknown),"
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
# Only once the program runs on the new Python, so a failed pip step keeps the old one. The
# wilma CLI runs on whichever Node is there, so an old Node goes with it.
for old in "$RUNTIMES"/python-* "$RUNTIMES"/node-*; do
  [ "$old" = "$RUNTIME" ] || [ "$old" = "$NODE_RUNTIME" ] || rm -rf "$old"
done
RECORD=("$APP/.venv/bin/python" -m family_brief.install_record)
"${RECORD[@]}" program "$APP" logs "$TARGET/logs" runtime "$RUNTIMES"
# The copy of the plugin the install line downloads, which the Codex skills run from; Claude
# Code manages its own copy.
if [ "$PLUGIN_ROOT" = "$TARGET/plugin" ]; then "${RECORD[@]}" plugin "$PLUGIN_ROOT"; fi

# --codex: also install the two skills for Codex, which reads user skills from ~/.agents/skills.
# They get unique names, Codex's $skill syntax, and a note saying where this plugin folder is.
# Without it, the setup page installs them once the family picks ChatGPT.
if [ "${1:-}" = "--codex" ]; then
  "$APP/.venv/bin/python" -m family_brief.chat_install codex-skills "$PLUGIN_ROOT"
fi

echo "✅ Parent Recap ${VERSION:-} installed to $APP"
echo "   Command: $APP/.venv/bin/parent-recap"
echo "   Real Python path (needed for the WhatsApp permission): $REAL_PY"
if [ -n "$OLD_REAL_PY" ] && [ "$OLD_REAL_PY" != "$REAL_PY" ]; then
  echo "⚠️  Parent Recap now runs on a new Python, so macOS no longer lets it read WhatsApp."
  echo "   If Parent Recap reads your WhatsApp groups, grant the permission again: System Settings →"
  echo "   Privacy & Security → Full Disk Access, press +, and add this Python:"
  echo "     $REAL_PY"
  echo "   (In the file picker, press ⌘⇧G and paste the path.) You can remove the old Python from that list."
fi
