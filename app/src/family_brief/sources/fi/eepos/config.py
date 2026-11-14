"""Eepos's part of the Household's config, its `eepos` section (ADR 0038), which
`family_brief.config` lists among the Sources' sections."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EeposSignin(BaseModel):
    """A guardian's sign-in at one school on Eepos (ADR 0038). Its password is in the system's
    secret store, never here (collector.secret_name). `kids` names the Kid each of the
    guardian's students is, by its id on Eepos, as `setup eepos` matched them by name; a student
    not in it is matched by name each evening."""
    school: str                     # the school's own part of its address: emo for emo.eepos.fi
    username: str
    kids: dict[str, str] = Field(default_factory=dict)

    @field_validator("school")
    @classmethod
    def _school(cls, v: str) -> str:
        from .collector import school_of
        school = school_of(v)
        if school is None:
            raise ValueError("not an Eepos school's address, such as emo for emo.eepos.fi")
        return school

    @field_validator("kids", mode="before")
    @classmethod
    def _ids(cls, v: object) -> object:
        return {str(k): kid for k, kid in v.items()} if isinstance(v, dict) else v


class EeposConfig(BaseModel):
    """Eepos, the music and art schools' system (ADR 0038): one sign-in per school, since each
    school on Eepos is its own address with its own session."""
    signins: list[EeposSignin] = Field(default_factory=list)
