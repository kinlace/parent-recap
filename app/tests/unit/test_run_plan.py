"""What tonight's evening run does, decided in one place: whether it ends quiet, with the short
Brief that says no Source could be read, or with the Brief, and whether a dry run or the setup
page's preview leaves any trace."""
from __future__ import annotations

import pytest

from family_brief.run import Options, Plan, plan

RUN, DRY_RUN, PREVIEW = Options(), Options(dry_run=True), Options(dry_run=True, preview=True)


@pytest.mark.parametrize("options, read, plan_", [
    # Nothing new: a quiet night, unless no Source could be read, which the parents are told.
    (RUN, dict(anything_new=False, nothing_could_be_read=False), Plan("quiet", sends=True)),
    (RUN, dict(anything_new=False, nothing_could_be_read=True), Plan("nothing_read", sends=True)),
    # A dry run decides the same, and sends and saves nothing.
    (DRY_RUN, dict(anything_new=False, nothing_could_be_read=False), Plan("quiet", sends=False)),
    (DRY_RUN, dict(anything_new=False, nothing_could_be_read=True), Plan("nothing_read", sends=False)),
    # The preview shows a quiet night's Brief too, so the family sees what one looks like.
    (PREVIEW, dict(anything_new=False, nothing_could_be_read=False), Plan("brief", sends=False, shows=True)),
    (PREVIEW, dict(anything_new=False, nothing_could_be_read=True), Plan("brief", sends=False, shows=True)),
    # Something new goes to the model, which hasn't written the Brief yet.
    (RUN, dict(anything_new=True, nothing_could_be_read=False), Plan("brief", sends=True)),
    (DRY_RUN, dict(anything_new=True, nothing_could_be_read=False), Plan("brief", sends=False)),
], ids=["quiet", "nothing read", "dry quiet", "dry nothing read", "preview of a quiet night",
        "preview of a night nothing was read", "something new", "dry, something new"])
def test_what_tonight_does_once_the_sources_are_read(options, read, plan_):
    assert plan(options, **read) == plan_


@pytest.mark.parametrize("options, written, plan_", [
    (RUN, dict(nothing_to_show=False, nothing_could_be_read=False), Plan("brief", sends=True)),
    # A Brief with nothing to show, such as an ordinary timetable alone, is as quiet as no news (#385).
    (RUN, dict(nothing_to_show=True, nothing_could_be_read=False), Plan("quiet", sends=True)),
    (DRY_RUN, dict(nothing_to_show=True, nothing_could_be_read=False), Plan("quiet", sends=False)),
    # When nothing could be read, the Brief's notes say so, though the model found nothing to say.
    (RUN, dict(nothing_to_show=True, nothing_could_be_read=True), Plan("brief", sends=True)),
    (DRY_RUN, dict(nothing_to_show=False, nothing_could_be_read=False), Plan("brief", sends=False)),
    (PREVIEW, dict(nothing_to_show=True, nothing_could_be_read=False), Plan("brief", sends=False, shows=True)),
], ids=["brief", "nothing to show", "dry, nothing to show", "nothing read, nothing to show", "dry brief",
        "preview with nothing to show"])
def test_what_tonight_does_once_the_brief_is_written(options, written, plan_):
    assert plan(options, anything_new=True, **written) == plan_
