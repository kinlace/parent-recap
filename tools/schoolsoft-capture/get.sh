#!/bin/bash
# SchoolSoft capture (#876): the one line a Swedish guardian pastes into Terminal on their Mac,
# with or without Parent Recap installed:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/kinlace/parent-recap/stable/tools/schoolsoft-capture/get.sh)"
#
# Puts its own Python (the pinned python-build-standalone build install.sh uses, checked against
# its checksum) and the tool's two files in ~/SchoolSoftCapture, with the command that removes
# everything again, ~/SchoolSoftCapture/remove, then runs the capture. Safe to run again.
# Downloads come from GitHub and Astral's mirror of python-build-standalone; the tool itself
# talks only to sms.schoolsoft.se (README.md beside this file).
set -euo pipefail

REPO="kinlace/parent-recap"
FILES="https://raw.githubusercontent.com/$REPO/stable"
FOLDER="$HOME/SchoolSoftCapture"

# The Python install.sh pins (ADR 0011), the two Mac builds only. Kept equal to install.sh's by
# tests/scripts/test_schoolsoft_capture_scripts.py: move them with it.
PYTHON_VERSION=3.13.16
PYTHON_BUILD=20261003
PYTHON_SHA256_AARCH64=d8975d7df4f08f7b1c7aafcdfacbddcec3d366415f2c1a72b2466b6850815933
PYTHON_SHA256_X86_64=8e9cb087305bfb8969f68a905f79f41469d4aa5220c1aa71ada7fc9953bdba0f
PYTHON_RELEASES=(
  https://releases.astral.sh/github/python-build-standalone/releases/download
  https://github.com/astral-sh/python-build-standalone/releases/download
)
PYTHON="$FOLDER/python-$PYTHON_VERSION-$PYTHON_BUILD"

failed() {
  echo "❌ $1"
  echo "   Run the line again. If it fails again, tell the Parent Recap team."
  echo "   Kör raden igen. Om det misslyckas igen, hör av dig till Parent Recap-teamet."
  echo "   请重新运行这一行。如果还是失败，请告诉 Parent Recap 团队。"
  exit 1
}

[ "$(uname)" = Darwin ] || { echo "❌ This runs on a Mac only. / Det här fungerar bara på en Mac. / 只能在 Mac 上运行。"; exit 1; }
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then
  ARCH=aarch64 SHA=$PYTHON_SHA256_AARCH64
else
  ARCH=x86_64 SHA=$PYTHON_SHA256_X86_64
fi

mkdir -p "$FOLDER"
chmod 700 "$FOLDER"
tmp=$(mktemp -d "$FOLDER/.download.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

if [ ! -x "$PYTHON/bin/python3" ]; then
  echo "Downloading Python for the capture (about 20 MB)… / Laddar ner Python (cirka 20 MB)… / 正在下载 Python（约 20 MB）……"
  file="cpython-$PYTHON_VERSION+$PYTHON_BUILD-$ARCH-apple-darwin-install_only.tar.gz"
  got=no
  for url in "${PYTHON_RELEASES[@]/%//$PYTHON_BUILD/$file}"; do
    if curl -fsSL --retry 2 -o "$tmp/python.tar.gz" "$url" 2>/dev/null; then got=yes; break; fi
  done
  [ "$got" = yes ] || failed "Python couldn't be downloaded (is this Mac online?)."
  [ "$(shasum -a 256 "$tmp/python.tar.gz" | cut -d' ' -f1)" = "$SHA" ] ||
    failed "The Python downloaded isn't the expected one (its checksum doesn't match), so it wasn't used."
  mkdir "$tmp/python"
  tar -xf "$tmp/python.tar.gz" -C "$tmp/python" --strip-components 1 2>/dev/null && [ -x "$tmp/python/bin/python3" ] ||
    failed "The Python downloaded couldn't be unpacked."
  rm -rf "$FOLDER"/python-*
  mv "$tmp/python" "$PYTHON"
fi

# The tool, Parent Recap's AI filter it masks with, and the command that removes everything.
for pair in "tools/schoolsoft-capture/schoolsoft_capture.py schoolsoft_capture.py" \
            "app/src/family_brief/ai_filter.py ai_filter.py" \
            "tools/schoolsoft-capture/remove.sh remove"; do
  read -r from to <<<"$pair"
  curl -fsSL --retry 2 -o "$tmp/$to" "$FILES/$from" 2>/dev/null || failed "The capture couldn't be downloaded (is this Mac online?)."
done
chmod 700 "$tmp/remove"
mv "$tmp/schoolsoft_capture.py" "$tmp/ai_filter.py" "$tmp/remove" "$FOLDER/"
rm -rf "$tmp"
trap - EXIT

exec "$PYTHON/bin/python3" -E -s "$FOLDER/schoolsoft_capture.py" capture "$@"
