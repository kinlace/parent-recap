"""How far an AI's installer has got, read from what it prints as it runs (#709): the share of its
download, from curl's progress meter or bar, or a line that ends in a percentage."""
from __future__ import annotations

import pytest

from family_brief import setup_ai


@pytest.mark.parametrize("line, share", [
    # curl's progress meter, on stderr when it writes to a file or a pipe: the first column.
    (" 45 12.3M   45 5612k    0     0  1234k      0  0:00:10  0:00:04  0:00:06 1300k", 45),
    ("100 12.3M  100 12.3M    0     0  2468k      0  0:00:05  0:00:05 --:--:-- 2600k", 100),
    ("  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0", 0),
    # curl's --progress-bar, and any other installer that ends a line with its percentage.
    ("##########################                                              37.5%", 37),
    ("Downloading codex-aarch64-apple-darwin.tar.gz 62 %", 62),
    ("Downloading: 8,5%", 8),
], ids=["meter", "meter-done", "meter-start", "bar", "spaced", "comma"])
def test_a_progress_line_gives_the_download_s_share(line, share):
    assert setup_ai.download_share(line) == share


@pytest.mark.parametrize("line", [
    "  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current",
    "                                 Dload  Upload   Total   Spent    Left  Speed",
    "Codex CLI 0.1.0 installed successfully.",
    "Writing web request",
    "Faster: 250%",
    "Step 1.2.75%",
    "",
], ids=["meter-head", "meter-head-2", "done", "powershell", "over-100", "dotted", "empty"])
def test_any_other_line_gives_none(line):
    assert setup_ai.download_share(line) is None


def test_the_share_is_the_last_one_printed_whole_across_pieces_and_carriage_returns():
    shares: list[int] = []
    reader = setup_ai.ShareReader(shares.append)

    for piece in [b"  % Total    % Received\r\n", b"\r  4", b"5 12.3M   45 5612k    0     0 ",
                  b" 1234k      0  0:00:10  0:00:04  0:00:06 1300k\r 9", b"0 12.3M   90 11.1M  "]:
        reader.feed(piece)

    # 90's line isn't over yet: its columns may still be coming.
    assert shares == [45]
    reader.feed(b"  0  0  0:00:10  0:00:09  0:00:01 1300k\n")
    assert shares == [45, 90]
