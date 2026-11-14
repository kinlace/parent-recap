"""Social Schools's part of the Household's config, its `socialschools` section (ADR 0049), which
`family_brief.config` lists among the Sources' sections."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SocialSchoolsSignin(BaseModel):
    """A parent's sign-in at Social Schools (ADR 0049): one account, whatever its schools. Its
    password is in the system's secret store, never here (collector.secret_name). `kids` names the
    Kid each of the parent's children is, by the child's name on Social Schools, as `setup
    socialschools` matched them; a child not in it is matched by name each evening."""
    username: str                   # the parent's email
    kids: dict[str, str] = Field(default_factory=dict)


class SocialSchoolsConfig(BaseModel):
    """Social Schools, the parent app of many schools in the Netherlands (ADR 0049)."""
    signins: list[SocialSchoolsSignin] = Field(default_factory=list)
    lookback_hours: int = 26  # older announcements are marked seen without being told
