#!/usr/bin/env bash
# The linux-install CI job's checks of install.sh and the program it installs, one per step:
# `scripts/linux-install-checks.sh <check>`. They live here rather than in the workflow so a change
# to the program changes no file under .github/, which an unattended agent's push can't change, as
# scripts/windows-install-checks.ps1 does for the windows-install job (#720, #721).
#
# A check fails by calling `fail` with what it saw, or by a command that fails on its own: either
# way the run's summary gets an annotation naming the check.
set -Eeuo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL="$REPO/install.sh"
INSTALLED="$HOME/ParentRecap"
PY="$INSTALLED/app/.venv/bin/python"
CHECK="${1:-}"

# Ends the check with an annotation on the run's summary; the step's name says which check it is.
fail() {
  local saw="$*"
  saw="${saw//'%'/%25}"
  saw="${saw//$'\r'/%0D}"
  saw="${saw//$'\n'/%0A}"
  echo "::error title=linux-install $CHECK::$saw"
  exit 1
}
trap 'fail "line $LINENO: $BASH_COMMAND exited with $?"' ERR

# Runs Python code on the installed program's Python, its output passed on, and fails with
# `$2` and that output when it exits with an error.
py() {
  local out
  if out="$("$PY" -c "$1" 2>&1)"; then
    echo "$out"
  else
    echo "$out"
    fail "$2: $out"
  fi
}

# ---------------------------------------------------------------- the checks

# A pin's value in install.sh, empty when it pins none. (A `fail` here would end only the $(...)
# that reads it, its annotation lost in the value.)
pin() {
  sed -n "s/^$1=//p" "$INSTALL"
}

# A line for a checksum pin that isn't the published one, so a bump can copy the published line;
# nothing when it is. $1 the pin's name, $2 the published list, $3 the file.
mismatch() {
  local pinned line
  pinned="$(pin "$1")"
  # The file name as it is: its + and . would be patterns to grep -E.
  line="$(awk -v file="$3" '$2 == file || $2 == "*" file' "$2")"
  [ "$pinned" = "${line%% *}" ] || echo "$3: install.sh pins $1=$pinned, the published list says: $line"
}

check_pins() {
  local python build node sums wrong
  python="$(pin PYTHON_VERSION)"
  build="$(pin PYTHON_BUILD)"
  node="$(pin NODE_VERSION)"
  [ -n "$python" ] && [ -n "$build" ] && [ -n "$node" ] ||
    fail "install.sh pins PYTHON_VERSION=$python PYTHON_BUILD=$build NODE_VERSION=$node"
  sums="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/sums.XXXXXX")"
  curl -fsSL "https://github.com/astral-sh/python-build-standalone/releases/download/$build/SHA256SUMS" > "$sums/python"
  curl -fsSL "https://nodejs.org/dist/v$node/SHASUMS256.txt" > "$sums/node"
  wrong="$(
    mismatch PYTHON_SHA256_LINUX_AARCH64 "$sums/python" "cpython-$python+$build-aarch64-unknown-linux-gnu-install_only.tar.gz"
    mismatch PYTHON_SHA256_LINUX_X86_64 "$sums/python" "cpython-$python+$build-x86_64-unknown-linux-gnu-install_only.tar.gz"
    mismatch NODE_SHA256_LINUX_ARM64 "$sums/node" "node-v$node-linux-arm64.tar.xz"
    mismatch NODE_SHA256_LINUX_X64 "$sums/node" "node-v$node-linux-x64.tar.xz"
  )"
  rm -r "$sums"
  [ -z "$wrong" ] || fail "$wrong"
}

check_install() {
  bash "$INSTALL" || fail "install.sh exited with $?"
  head -1 "$INSTALLED"/logs/install-*.log
}

check_own_runtimes() {
  "$INSTALLED/app/.venv/bin/parent-recap" --help > /dev/null || fail "parent-recap --help exited with $?"
  py "import sys; from family_brief import own_node; n = own_node.node(); print(n, own_node.npm()); sys.exit(n is None or own_node.npm() is None)" \
    "the program doesn't find its own Node"
  py "import os, sys; p = os.path.realpath(sys._base_executable); print(p); sys.exit('ParentRecap' not in p)" \
    "the venv isn't on Parent Recap's own Python"
}

check_login_shell() {
  local found
  found="$(bash -l -c 'command -v parent-recap')" || fail "a new login shell finds no parent-recap"
  echo "parent-recap: $found"
  [ "$(readlink -f "$found")" = "$(readlink -f "$INSTALLED/app/.venv/bin/parent-recap")" ] ||
    fail "a new login shell finds parent-recap at $found"
  bash -l -c 'parent-recap --help' > /dev/null || fail "parent-recap --help exited with $? in a new login shell"
}

# As the setup page's Wilma step installs it, from npm, with the program's own Node.
check_wilma() {
  py "import sys; from family_brief import setup_wilma; r = setup_wilma.install(); print(r); sys.exit(r['result'] != 'installed')" \
    "the wilma CLI didn't install"
  py "import subprocess, sys; from family_brief import own_node; c = own_node.wilma(); print(c); sys.exit(subprocess.run([*c, '--help'], env=own_node.wilma_env()).returncode)" \
    "wilma --help failed"
}

# ADR 0030: the file is the Household user's alone.
check_secret_file() {
  local where mode folder
  where="$("$PY" -c "from family_brief import gateway; from family_brief.system import secrets; secrets.put(gateway.ACCOUNT, 'sk-ci-not-a-real-key'); print(secrets.where(gateway.ACCOUNT))")" ||
    fail "the Gateway Key wasn't stored"
  echo "$where"
  mode="$(stat -c %a "$where")"
  folder="$(stat -c %a "$(dirname "$where")")"
  [ "$mode" = 600 ] || fail "$where is mode $mode, not 600"
  [ "$folder" = 700 ] || fail "$(dirname "$where") is mode $folder, not 700"
  [[ "$where" == "$HOME/.family/secrets/"* ]] || fail "stored at $where, not under $HOME/.family/secrets/"
}

check_second_install() {
  local before after
  before="$(stat -c '%n %Y' "$INSTALLED"/runtime/*)"
  bash "$INSTALL" || fail "install.sh exited with $?"
  after="$(stat -c '%n %Y' "$INSTALLED"/runtime/*)"
  [ "$after" = "$before" ] || fail "the second install changed the runtimes: before $before, after $after"
}

# ---------------------------------------------------------------- running one

case "$CHECK" in
  pins) check_pins ;;
  install) check_install ;;
  own-runtimes) check_own_runtimes ;;
  login-shell) check_login_shell ;;
  wilma) check_wilma ;;
  secret-file) check_secret_file ;;
  second-install) check_second_install ;;
  *)
    trap - ERR
    echo "usage: $0 pins|install|own-runtimes|login-shell|wilma|secret-file|second-install" >&2
    exit 2
    ;;
esac
