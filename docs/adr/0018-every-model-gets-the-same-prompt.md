# Every model gets the same prompt

The summarize and translate prompts are one set of text for every backend and model: the claude CLI's default, Haiku 5.5 behind the gateway, and Codex. When a model gets something wrong, we fix the shared rule and score the change on every model with the eval (`--model default,claude-haiku-5-5`), instead of writing a prompt per model. The mistakes found so far are rules missing for every model, not one model's quirks (#285, #286, #287); the models behind a backend change without a release (the CLI's default, the gateway's config), so a prompt tuned to one would drift as soon as it is swapped; and each extra prompt would double the eval and let the copies quietly diverge.

## Considered Options

- **A prompt per model or backend.** Rejected for the reasons above. The baseline in #289 found no rule only one model needs.
- **A short per-backend addendum.** Kept as a last resort: only when the eval shows, again and again, a rule that helps one model and hurts another, and no shared wording fixes both. Rules already vary by Brief language (the Finnish-terms rule), never by model.
