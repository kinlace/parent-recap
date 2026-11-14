"""Konnect's part of the Household's config, its `konnect` section (ADR 0050), which
`family_brief.config` lists among the Sources' sections."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class KonnectSignin(BaseModel):
    """A parent's sign-in at one provider's Konnect parent portal (ADR 0050). Its password is in
    the system's secret store, never here (collector.secret_name). `kids` names the Kid each of
    the parent's children is, by its id on Konnect, as `setup konnect` matched them by name; a
    child not in it is matched by name each evening."""
    portal: str                     # the provider's own part of its address: partou for partou.ouderportaal.nl
    username: str
    kids: dict[str, str] = Field(default_factory=dict)

    @field_validator("portal")
    @classmethod
    def _portal(cls, v: str) -> str:
        from .collector import portal_of
        portal = portal_of(v)
        if portal is None:
            raise ValueError("not a Konnect portal's address, such as partou for partou.ouderportaal.nl")
        return portal

    @field_validator("kids", mode="before")
    @classmethod
    def _ids(cls, v: object) -> object:
        return {str(k): kid for k, kid in v.items()} if isinstance(v, dict) else v


class KonnectConfig(BaseModel):
    """Konnect's parent portal, which Dutch childcare and after-school care (BSO) providers run at
    `<provider>.ouderportaal.nl` (ADR 0050): one sign-in per provider, each with its own session."""
    signins: list[KonnectSignin] = Field(default_factory=list)
    lookback_hours: int = 26  # older messages are marked seen without being told
