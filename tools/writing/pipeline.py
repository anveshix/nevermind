#!/usr/bin/env python3
"""Human-writing pipeline: Draft → Humanize → AI-writing audit → Fact verification → Final.

Stdlib only (Python 3.9+). Needs Node.js 18+ for the bundled avoid-ai-writing detector.
The rewrite stages (humanizer, avoid-ai-writing) are LLM skills run from Claude Code;
this CLI supplies the deterministic gates around them and refuses to finalize output
that drops or invents facts, or carries unverified claims.

Usage:
  pipeline.py init   DRAFT --type TYPE [--name NAME]     # stage 1: draft + baseline audit
  pipeline.py audit  FILE  --type TYPE                    # audit any file (detector JSON summary)
  pipeline.py check  WORKDIR --final FILE                 # stage 3: fact-preservation gate
  pipeline.py claims WORKDIR                              # stage 4: extract claims to verify
  pipeline.py verify WORKDIR --sources SOURCES.json       # stage 4: record verification results
  pipeline.py finalize WORKDIR                            # stage 5: all gates → 06_final.md
Exit codes: 0 pass, 1 gate failed, 2 usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PROFILES = json.loads((HERE / "profiles.json").read_text(encoding="utf-8"))
DETECTOR = ROOT / ".claude" / "skills" / "avoid-ai-writing" / "bin" / "avoid-ai-writing.js"
WORK_ROOT = ROOT / "output" / "writing"
MAX_BYTES = 2 * 1024 * 1024

NUM_RE = re.compile(r"(?:Rs\.?\s?|₹\s?)?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|lakh|crore|lakhs|crores))?")
URL_RE = re.compile(r"https?://\S+")
QUOTE_RE = re.compile(r"[\"“”]([^\"“”]{3,200})[\"“”]")
PROPER_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+)*)")
GREETING_RE = re.compile(r"^(Dear|Yours|Regards|Kind regards|Sincerely|Thank you|Thanks|Best)\b", re.IGNORECASE)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
COMMON = {"The", "A", "An", "This", "That", "These", "It", "In", "On", "For", "And", "But",
          "Or", "If", "When", "Our", "We", "You", "I", "He", "She", "They", "Sir", "Madam",
          "Dear", "Yours", "Regards", "Thank", "Please", "Note", "Also", "However", "Today",
          "Here", "There", "What", "Why", "How", "Who", "Each", "All", "Some", "Many"}


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def read_text(path: Path) -> str:
    if not path.is_file():
        die(f"file not found: {path}")
    if path.stat().st_size > MAX_BYTES:
        die(f"file larger than {MAX_BYTES // 1024 // 1024} MB: {path}")
    return path.read_text(encoding="utf-8")


def profile_for(content_type: str) -> dict[str, Any]:
    if content_type not in PROFILES["content_types"]:
        die(f"unknown --type {content_type!r}; choose: {', '.join(PROFILES['content_types'])}")
    return PROFILES["content_types"][content_type]


def run_detector(path: Path, context: str) -> dict[str, Any]:
    """Score a file with the vendored detector. Read-only; never edits the file."""
    if not DETECTOR.is_file():
        die(f"detector missing at {DETECTOR}; reinstall per docs/WRITING_PIPELINE.md")
    node = shutil.which("node")
    if not node:
        die("Node.js 18+ is required for the avoid-ai-writing detector")
    try:
        proc = subprocess.run([node, str(DETECTOR), "--context", context, "--", str(path)],
                              capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        die("detector timed out after 60s")
    if proc.returncode != 0:
        die(f"detector failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)
    issues = data.get("issues") or []
    return {"score": data.get("score"), "label": data.get("label"), "issue_count": len(issues),
            "issues": [{"text": i.get("text"), "type": i.get("type"), "severity": i.get("severity")}
                       for i in issues]}


def extract_facts(text: str) -> dict[str, set[str]]:
    """Facts that must survive rewriting unchanged: numbers, URLs, quotes, names, protected terms."""
    facts: dict[str, set[str]] = {"numbers": set(), "urls": set(), "quotes": set(),
                                  "names": set(), "terms": set()}
    for m in NUM_RE.finditer(text):
        facts["numbers"].add(re.sub(r"[\s,]", "", m.group(0)).replace("Rs.", "Rs").replace("₹", "Rs"))
    facts["urls"] = {u.rstrip(".,;)") for u in URL_RE.findall(text)}
    facts["quotes"] = {q.strip() for q in QUOTE_RE.findall(text)}
    for sent in SENT_SPLIT.split(text):
        for m in PROPER_RE.finditer(sent):
            name = m.group(1).strip()
            words = [w for w in name.split() if w not in COMMON]
            if words:
                facts["names"].add(" ".join(words))
    for term in protected_terms():
        if re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text):
            facts["terms"].add(term)
    return facts


def protected_terms() -> list[str]:
    return PROFILES["protected_terms"]


def check_preservation(draft: str, final: str) -> dict[str, Any]:
    d, f = extract_facts(draft), extract_facts(final)
    fail: dict[str, list[str]] = {}
    warn: dict[str, list[str]] = {}
    # Hard gates: dropped or changed facts, and anything new that could be invented.
    for key in ("numbers", "urls", "quotes", "terms"):
        missing = sorted(d[key] - f[key])
        if missing:
            fail[f"missing_{key}"] = missing
    for key in ("numbers", "urls", "quotes", "names"):
        added = sorted(f[key] - d[key])
        if added:
            fail[f"added_{key}"] = added
    # Soft gate: proper names dropped (may be legitimate if redundant).
    dropped_names = sorted(d["names"] - f["names"])
    if dropped_names:
        warn["missing_names"] = dropped_names
    return {"pass": not fail, "fail": fail, "warn": warn,
            "draft_facts": {k: len(v) for k, v in d.items()},
            "final_facts": {k: len(v) for k, v in f.items()}}


def split_claims(text: str) -> list[str]:
    """Sentences that assert a checkable fact (number, date, name, quote, URL, or citation)."""
    claims = []
    for sent in SENT_SPLIT.split(text):
        s = sent.strip().lstrip("#-*> ").strip()
        if len(s) < 12 or GREETING_RE.match(s) or s.endswith(",") or (len(s.split()) < 6 and not re.search(r"[.!?]$", s)):
            continue
        if (NUM_RE.search(s) or URL_RE.search(s) or QUOTE_RE.search(s)
                or re.search(r"\b(19|20)\d{2}\b", s) or PROPER_RE.search(s)):
            claims.append(s)
    return claims


def load_manifest(workdir: Path) -> dict[str, Any]:
    p = workdir / "manifest.json"
    if not p.is_file():
        die(f"not a pipeline workdir (missing manifest.json): {workdir}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(workdir: Path, m: dict[str, Any]) -> None:
    m["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (workdir / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_init(a: argparse.Namespace) -> int:
    prof = profile_for(a.type)
    draft = Path(a.draft)
    text = read_text(draft)
    name = a.name or re.sub(r"[^A-Za-z0-9_-]+", "-", draft.stem)[:60]
    workdir = WORK_ROOT / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{name}"
    workdir.mkdir(parents=True, exist_ok=False)
    (workdir / "01_draft.md").write_text(text, encoding="utf-8")
    audit = run_detector(workdir / "01_draft.md", prof["detector_context"])
    (workdir / "02_audit_draft.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    save_manifest(workdir, {"content_type": a.type, "voice": prof["voice"],
                            "detector_context": prof["detector_context"], "max_score": prof["max_score"],
                            "stage": "drafted", "draft_score": audit["score"], "draft_sha": None})
    print(f"WORKDIR {workdir}")
    print(f"Baseline audit: score={audit['score']} ({audit['label']}), issues={audit['issue_count']}")
    print("Next: run /humanizer then /avoid-ai-writing on 01_draft.md; save result as 03_humanized.md;")
    print(f"      then: python3 tools/writing/pipeline.py check {workdir} --final {workdir}/03_humanized.md")
    return 0


def cmd_audit(a: argparse.Namespace) -> int:
    prof = profile_for(a.type)
    res = run_detector(Path(a.file), prof["detector_context"])
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    wd = Path(a.workdir)
    m = load_manifest(wd)
    final_src = Path(a.final)
    final_text = read_text(final_src)
    final_copy = wd / "03_humanized.md"
    if final_src.resolve() != final_copy.resolve():
        final_copy.write_text(final_text, encoding="utf-8")
    draft = read_text(wd / "01_draft.md")
    pres = check_preservation(draft, final_text)
    audit = run_detector(final_copy, m["detector_context"])
    (wd / "04_preservation.json").write_text(json.dumps(pres, indent=2, ensure_ascii=False), encoding="utf-8")
    (wd / "05_audit_final.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    audit_ok = audit["score"] is not None and audit["score"] <= m["max_score"] and \
        audit["score"] <= (m.get("draft_score") or 100)
    m["stage"] = "checked"
    m["preservation_pass"] = pres["pass"]
    m["final_score"] = audit["score"]
    m["audit_pass"] = audit_ok
    save_manifest(wd, m)
    print(json.dumps({"preservation_pass": pres["pass"], "fail": pres["fail"], "warn": pres["warn"],
                      "final_score": audit["score"], "draft_score": m.get("draft_score"),
                      "max_score": m["max_score"], "audit_pass": audit_ok}, indent=2, ensure_ascii=False))
    return 0 if (pres["pass"] and audit_ok) else 1


def cmd_claims(a: argparse.Namespace) -> int:
    wd = Path(a.workdir)
    load_manifest(wd)
    final = read_text(wd / "03_humanized.md") if (wd / "03_humanized.md").is_file() \
        else read_text(wd / "01_draft.md")
    claims = [{"id": f"C{i}", "text": s, "status": "UNVERIFIED", "source": ""}
              for i, s in enumerate(split_claims(final), 1)]
    (wd / "claims.json").write_text(json.dumps(claims, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Fact verification worksheet", "",
             "Status must be set per claim with `verify --sources`. Allowed: verified (needs source), refuted, unsupported.", ""]
    lines += [f"- [ ] **{c['id']}** {c['text']}  — `UNVERIFIED`" for c in claims]
    (wd / "05_claims.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(claims)} claims → {wd / '05_claims.md'}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    wd = Path(a.workdir)
    load_manifest(wd)
    claims = json.loads((wd / "claims.json").read_text(encoding="utf-8"))
    try:
        src = json.loads(Path(a.sources).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read sources file: {e}")
    allowed = {"verified", "refuted", "unsupported"}
    for c in claims:
        r = src.get(c["id"])
        if not r:
            continue
        status = str(r.get("status", "")).lower()
        if status not in allowed:
            die(f"{c['id']}: status must be one of {sorted(allowed)}")
        if status == "verified" and not str(r.get("source", "")).strip():
            die(f"{c['id']}: 'verified' requires a source reference")
        c["status"] = status.upper()
        c["source"] = r.get("source", "")
    (wd / "claims.json").write_text(json.dumps(claims, indent=2, ensure_ascii=False), encoding="utf-8")
    counts: dict[str, int] = {}
    for c in claims:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    print(json.dumps(counts, indent=2))
    return 0


def cmd_finalize(a: argparse.Namespace) -> int:
    wd = Path(a.workdir)
    m = load_manifest(wd)
    problems = []
    if not m.get("preservation_pass"):
        problems.append("preservation check failed or not run (see 04_preservation.json)")
    if not m.get("audit_pass"):
        problems.append(f"audit gate failed: final score {m.get('final_score')} vs max {m['max_score']}")
    claims = json.loads((wd / "claims.json").read_text(encoding="utf-8")) if (wd / "claims.json").is_file() else None
    if claims is None:
        problems.append("claims not extracted (run `claims`)")
    else:
        bad = [c["id"] for c in claims if c["status"] != "VERIFIED"]
        if bad:
            problems.append(f"{len(bad)} claim(s) not verified: {', '.join(bad[:20])}")
    if problems:
        for p in problems:
            print(f"BLOCKED: {p}", file=sys.stderr)
        return 1
    shutil.copyfile(wd / "03_humanized.md", wd / "06_final.md")
    m["stage"] = "final"
    save_manifest(wd, m)
    print(f"FINAL {wd / '06_final.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    types = list(PROFILES["content_types"])
    s = sub.add_parser("init"); s.add_argument("draft"); s.add_argument("--type", required=True, choices=types); s.add_argument("--name")
    s = sub.add_parser("audit"); s.add_argument("file"); s.add_argument("--type", required=True, choices=types)
    s = sub.add_parser("check"); s.add_argument("workdir"); s.add_argument("--final", required=True)
    s = sub.add_parser("claims"); s.add_argument("workdir")
    s = sub.add_parser("verify"); s.add_argument("workdir"); s.add_argument("--sources", required=True)
    s = sub.add_parser("finalize"); s.add_argument("workdir")
    a = p.parse_args(argv)
    handler = {"init": cmd_init, "audit": cmd_audit, "check": cmd_check,
               "claims": cmd_claims, "verify": cmd_verify, "finalize": cmd_finalize}[a.cmd]
    return handler(a)


if __name__ == "__main__":
    sys.exit(main())
