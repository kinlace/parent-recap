# The AI backend is one module with an adapter per provider

Today `call_llm` dispatches a prompt to Claude, Codex or the gateway through one table, but everything else a backend needs sits beside it in `summarize.py` and is imported from eight other modules: finding the program, checking it is signed in, the host `doctor` probes, the install hint, the name the Brief shows. Four places compare `llm.backend` by string outside the dispatch. From the next refactoring the backends live in their own package, `llm`, behind one interface that covers all of those concerns, with one adapter per provider and an in-memory adapter for tests. The rest of the program, including the setup page and `doctor`, asks the backend module and never a provider by name. Running a prompt stays as it is: `call_llm_filtered` applies the AI filter once, in front of the seam (ADR 0013), and every provider gets the same prompt (ADR 0018).

## Considered Options

- **Keep the dispatch table and tidy the helpers in place.** Cheaper, but a fourth provider would still touch the same eight modules, and the tests would still fake the model by matching the CLI's arguments on a patched `subprocess.run`.

## Consequences

- The test harness fakes the backend through the in-memory adapter; the test that proves Claude runs isolated from the family's own session moves into the Claude adapter's own tests.
- A Household still uses exactly one backend and never falls back (ADR 0017); the module does not add a chooser.
