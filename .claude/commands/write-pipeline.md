---
description: Run the Draft → Humanize → AI-audit → Fact-verify → Final pipeline on a file
argument-hint: <draft-file> <content_type>
---
Run the human-writing pipeline on `$ARGUMENTS` (draft file, then content type: cbse_material | lesson_plan | official_letter | report | article | social_media | conversational).

1. `python3 tools/writing/pipeline.py init <draft> --type <type>` → note the WORKDIR.
2. Humanize: apply the `humanizer` skill to `WORKDIR/01_draft.md`, then the `avoid-ai-writing` skill (voice from profiles.json, mode: rewrite, iterate 2). Keep every number, name, date, quote, URL and Indian-English form (Rs, lakh, crore, Sir/Madam). Do not add facts, names or anecdotes. Save to `WORKDIR/03_humanized.md`.
3. `python3 tools/writing/pipeline.py check WORKDIR --final WORKDIR/03_humanized.md`. On failure, fix the text and repeat step 2; do not edit the gate.
4. `python3 tools/writing/pipeline.py claims WORKDIR`, then check each claim in `05_claims.md` against a real source. Write `sources.json` with `verified` plus a source reference, `refuted`, or `unsupported`, then `verify`. Never mark a claim verified without a source.
5. `python3 tools/writing/pipeline.py finalize WORKDIR` → `06_final.md`. If blocked, report the blocking claims to the user; do not finalize.
