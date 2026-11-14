"""eVaka's part of the Household's config, its `evaka` section (ADR 0039), which
`family_brief.config` lists among the Sources' sections."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EvakaSignin(BaseModel):
    """A guardian's sign-in in one city's eVaka (ADR 0039). Its password is in the system's secret
    store, never here (collector.secret_name). `kids` names the Kid each of the guardian's
    children is, by its id on eVaka, as `setup evaka` matched them by name; a child not in it is
    matched by name each evening."""
    city: str                       # a city's name in collector.CITIES, or its eVaka host
    username: str                   # the guardian's email
    kids: dict[str, str] = Field(default_factory=dict)

    @field_validator("city")
    @classmethod
    def _city(cls, v: str) -> str:
        from .collector import city_of
        city = city_of(v)
        if city is None:
            raise ValueError("not a city on eVaka, such as espoo, or its host")
        return city


class EvakaConfig(BaseModel):
    """eVaka, the cities' early-childhood education system (ADR 0039): one sign-in per city, since
    each city runs it at a host of its own, with sessions of its own."""
    signins: list[EvakaSignin] = Field(default_factory=list)
    lookback_hours: int = 26  # older messages are marked seen without being told
