"""The Eepos Source (ADR 0038), all of its own Python in this folder (ADR 0051): how it is read
(`collector`), its part of the Household's config (`config`), its `setup eepos` step (`setup`),
what `doctor` checks of it (`doctor`) and its record in the registry of Sources (`record`). Shared
code names this folder only where it lists the Sources: the registry's list, the config's section
and the list of setup commands. The setup page's and the hosted sign-up's Eepos steps stay theirs
until those pages are split by Source, and reach this folder through its record (`Source.code`)."""
