# Human-Writing Pipeline

Draft → **Humanize** (`humanizer`) → **AI-writing audit** (`avoid-ai-writing`) → **Fact verification** → **Final**

Scope: a writing-quality tool for educational and official text. It does not aim to evade AI detectors, and detector scores are signals, not verdicts. Its gates do not check tone; a human reads the final text.

## Components

| Piece | Path | Role |
|---|---|---|
| humanizer skill | `.claude/skills/humanizer/SKILL.md` | LLM rewrite of AI tells (Wikipedia "Signs of AI writing") |
| avoid-ai-writing skill | `.claude/skills/avoid-ai-writing/` | Rewrite/detect/edit modes, voice profiles, references |
| detector (Node, no deps) | `.claude/skills/avoid-ai-writing/bin/avoid-ai-writing.js` | Deterministic score, read-only |
| pipeline CLI (stdlib Python) | `tools/writing/pipeline.py` | Stage state, fact-preservation gate, claim worksheet, finalize gate |
| content profiles | `tools/writing/profiles.json` | Content type → detector context, voice, protected Indian-English terms |
| tests | `tools/writing/test_pipeline.py` | Gate behaviour |
| slash command | `.claude/commands/write-pipeline.md` | Runs the whole pipeline |
| pins | `.claude/skills/PINS.md` | Source commits |

## Installation

Both skills are vendored (copied, not installed by a package manager) so the project is reproducible. Pinned commits are in `.claude/skills/PINS.md`.

```bash
# Reproduce the vendored copies
git clone https://github.com/blader/humanizer /tmp/h && git -C /tmp/h checkout a32f24407364c74c812b43c15926d638bee1e072
git clone https://github.com/conorbronsdon/avoid-ai-writing /tmp/a && git -C /tmp/a checkout a5d6bae7c0d762dbcff74ca86c17a7ac9fee4151
mkdir -p .claude/skills/humanizer .claude/skills/avoid-ai-writing
cp /tmp/h/SKILL.md /tmp/h/LICENSE .claude/skills/humanizer/
cp -r /tmp/a/SKILL.md /tmp/a/LICENSE /tmp/a/references /tmp/a/detector /tmp/a/bin /tmp/a/examples .claude/skills/avoid-ai-writing/
```

Requirements: Python 3.9+ and Node.js 18+ (the detector only; tested on Node 22).

**Do not also install these at user scope** (`~/.claude/skills/`) or through `/plugin marketplace add blader/humanizer` / `/plugin install avoid-ai-writing`. Two copies of the same skill name collide. Use one install path per machine. If you prefer the plugin route for a personal setup, remove the project copies first.

Plugin alternatives, if you want them outside this repo: `/plugin marketplace add blader/humanizer` then `/plugin install humanizer@humanizer` (needs Claude Code 2.1.142+); `npx skills add conorbronsdon/avoid-ai-writing`.

## Usage

Skills, in Claude Code:
```
/humanizer                  # paste text, or name a file
/avoid-ai-writing           # rewrite mode; add "detect" for audit-only, or name a file for edit-in-place
```

Full pipeline:
```
/write-pipeline output/drafts/letter.md official_letter
```
or manually:
```bash
python3 tools/writing/pipeline.py init DRAFT.md --type cbse_material        # → WORKDIR, baseline score
# humanize + audit → WORKDIR/03_humanized.md (via the two skills)
python3 tools/writing/pipeline.py check WORKDIR --final WORKDIR/03_humanized.md
python3 tools/writing/pipeline.py claims WORKDIR                            # → 05_claims.md
python3 tools/writing/pipeline.py verify WORKDIR --sources sources.json     # verified|refuted|unsupported
python3 tools/writing/pipeline.py finalize WORKDIR                          # → 06_final.md or BLOCKED
python3 tools/writing/pipeline.py audit FILE --type social_media            # audit only
```
Content types: `cbse_material`, `lesson_plan`, `official_letter`, `report`, `article`, `social_media`, `conversational`.
Outputs go to `output/writing/` (git-ignored).

### What the gates enforce
- **Preservation (hard):** every number, URL, quoted string and protected term in the draft must survive; no new number, URL, quote or capitalised name may appear. Dropped names only warn.
- **Audit (hard):** final score ≤ the type's `max_score` and not higher than the draft's.
- **Claims (hard):** every claim needs a `verified` status with a source reference. Refuted or unsupported claims block finalize until removed from the text.

## Configuration
- Thresholds, detector context and voice per type: `tools/writing/profiles.json`.
- Protected terms: `protected_terms` in the same file (add school codes, exam names, etc.).
- Nothing reads secrets or the network; the detector is local.

## Rollback
```bash
rm -rf .claude/skills/humanizer .claude/skills/avoid-ai-writing .claude/skills/PINS.md
rm -rf tools/writing .claude/commands/write-pipeline.md docs/WRITING_PIPELINE.md output/writing
# .gitignore: restore the single line ".claude/" (remove the "!.claude/skills/" and "!.claude/commands/" exceptions)
```

## Tests
```bash
python3 -m unittest discover -s tools/writing -v
```

## Troubleshooting
- `detector missing`: re-run the vendoring steps.
- `Node.js 18+ is required`: install Node 18 or newer, or the audit stage cannot run.
- Skill appears twice in `/` menu: a user-scope or plugin copy also exists; remove one.
- `BLOCKED: N claim(s) not verified`: expected. Add source references in `sources.json`, or remove the claim from the text.
