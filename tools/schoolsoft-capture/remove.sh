#!/bin/bash
# Removes everything the SchoolSoft capture (#876) stored on this Mac: the sign-in in the
# Keychain, the evening check's launchd job and the ~/SchoolSoftCapture folder with its files.
# get.sh puts it at ~/SchoolSoftCapture/remove; it needs nothing else, and is safe to run again.
# Without that folder, the same from GitHub:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/kinlace/parent-recap/stable/tools/schoolsoft-capture/remove.sh)"
#
# The names below are the tool's: KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, LABEL and FOLDER in
# schoolsoft_capture.py, kept equal by tests/scripts/test_schoolsoft_capture_scripts.py.
set -uo pipefail

LABEL="com.kinlace.schoolsoft-capture"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# The job first, so no evening check starts while the rest goes.
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1
rm -f "$PLIST"
security delete-generic-password -s schoolsoft-capture -a refresh-token >/dev/null 2>&1
rm -rf "$HOME/SchoolSoftCapture"

if [ -e "$PLIST" ] || [ -e "$HOME/SchoolSoftCapture" ] ||
   security find-generic-password -s schoolsoft-capture -a refresh-token >/dev/null 2>&1; then
  echo "❌ Not everything could be removed. Tell the Parent Recap team."
  echo "   Allt gick inte att ta bort. Hör av dig till Parent Recap-teamet."
  echo "   有些内容没能删除。请告诉 Parent Recap 团队。"
  exit 1
fi
echo "✅ Everything the SchoolSoft capture stored is removed."
echo "   Allt som SchoolSoft-insamlingen sparade är borttaget."
echo "   SchoolSoft 采集工具保存的所有内容都已删除。"
