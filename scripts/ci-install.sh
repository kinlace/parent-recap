#!/usr/bin/env bash
# Builds the test environment in the Python that `python` runs (a venv), as the test, windows and
# linux CI jobs do, and as a contributor does once before running the tests (CONTRIBUTING.md). It
# lives here rather than in the workflow so a change to how CI installs packages changes no file
# under .github/ (#940).
#
# As install.sh installs: prebuilt packages only, at the pinned versions and checked against their
# checksums, pip first, with the google extra a Google Calendar Household gets; then the program
# from app/ with nothing of its own (CONTRIBUTING.md, "Pinned packages").
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../app"

python -m pip install --only-binary :all: --require-hashes -c constraints.txt pip
# shellcheck disable=SC2046 # one argument per package name
python -m pip install --only-binary :all: --require-hashes -c constraints.txt $(python -I requirements.py test google)
python -m pip install --no-deps --no-build-isolation -e .
