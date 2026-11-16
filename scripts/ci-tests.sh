#!/usr/bin/env bash
# Runs one test layer (app/tests/README.md) as the test, windows and linux CI jobs do, one step a
# layer: `scripts/ci-tests.sh <layer> [--coverage]`. The markers and pytest flags live here rather
# than in the workflow so a change to them changes no file under .github/ (#940).
#
# Every layer lists its slowest tests, so a run that got slower shows which test did it. The slow
# layers run on every core (pytest-xdist's -n auto); the fast ones take seconds, less than starting
# the workers would save (#509).
#
# --coverage (the macOS job) adds each layer to the same coverage data, in the order the job runs
# them: unit starts it, browser, the last, prints the table (lines and branches per module,
# pyproject.toml's [tool.coverage]) and writes the HTML report, with no threshold (#403).
# pytest-cov combines the workers' coverage.
set -euo pipefail

LAYER="${1:-}"
COVERAGE="${2:-}"

usage() {
  echo "usage: $0 <layer> [--coverage], a layer of:" \
    "unit integration e2e e2e-windows adapters scripts browser" >&2
  exit 2
}
[ $# -le 2 ] || usage

case "$LAYER" in
  unit) args=(-m unit) ;;
  integration) args=(-m integration) ;;
  # The browser tests are their own layer, run last.
  e2e) args=(-m "e2e and not browser" -n auto) ;;
  # The windows job's: the end-to-end tests that need Windows run nowhere else; those marked
  # windows_too, such as the update's on a PC (#993), run on every system. In one process.
  e2e-windows) args=(-m "e2e and (windows or windows_too)") ;;
  adapters) args=(-m adapters) ;;
  # Includes the version-consistency check (tests/scripts/test_version.py).
  scripts) args=(-m scripts -n auto) ;;
  # The setup page in a real browser (ADR 0024). -rs prints why a test skipped, so a run that
  # skipped them all says so.
  browser) args=(-m browser -n auto -rs) ;;
  *) usage ;;
esac
args+=(--durations=15)

case "$COVERAGE" in
  "") ;;
  --coverage)
    case "$LAYER" in
      unit) args+=(--cov --cov-report=) ;;
      browser) args+=(--cov --cov-append --cov-report=term --cov-report=html) ;;
      *) args+=(--cov --cov-append --cov-report=) ;;
    esac ;;
  *) usage ;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/../app"
# The command as it can be pasted, the marker quoted.
printf 'python -m pytest'; printf ' %q' "${args[@]}"; echo
exec python -m pytest "${args[@]}"
