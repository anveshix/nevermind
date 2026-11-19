"""Jenni bridge — voice/chat front-end ⇄ NeverMind multi-agent OS.

Chat routing (synergy):
  1. Teaching-deliverable commands (question paper, worksheet, lesson plan, …)
     → NeverMind orchestrator (9 agents, validators, constitution, HITL).
  2. Everything else → configured LLM (Settings key, or AI_* env vars) with
     Jenni persona; with no key it falls back to the offline agents (zero-key).
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from .db import DBI
from .llm import OpenAICompatibleEngine
from .orchestrator import ORCH_INTENT_CHAT_SKIP, classify_intent, detect_lang

SYSTEM_PROMPT = (
    "You are Jenni, a warm, concise assistant for educators, working inside "
    "NeverMind. Reply in the user's language (English, Hindi or Hinglish). "
    "You cannot send messages, save files or access external accounts; never "
    "claim you did. For worksheets, question papers, lesson plans, notices or "
    "quizzes, tell the user to ask for them directly so NeverMind's validated "
    "agents build them."
)
WAIT_SECONDS = 25


def _engine() -> Tuple[Optional[OpenAICompatibleEngine], bool]:
    llm = DBI.get_setting("llm", {}) or {}
    key = llm.get("api_key") or os.environ.get("AI_API_KEY", "")
    base = llm.get("base_url") or os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
    model = llm.get("model") or os.environ.get("AI_MODEL", "gpt-4o-mini")
    local = base.startswith(("http://localhost", "http://127.0.0.1"))
    if base.startswith("http://") and not local:
        return None, False                       # refuse cleartext remote providers
    if key or local:
        return OpenAICompatibleEngine(base, key or "local", model), True
    return None, False


def health() -> Dict[str, Any]:
    eng, ok = _engine()
    voice_key = bool((DBI.get_setting("voice", {}) or {}).get("api_key")
                     or (DBI.get_setting("llm", {}) or {}).get("api_key"))
    return {"configured": True, "llm": ok,
            "model": eng.model if eng else "NeverMind offline agents",
            "transcriptionConfigured": voice_key, "agents": True}


def parse_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    msgs = payload.get("messages")
    if not isinstance(msgs, list) or not 1 <= len(msgs) <= 20:
        raise ValueError("Send between 1 and 20 recent chat messages.")
    out = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") \
                or not isinstance(m.get("content"), str):
            raise ValueError("Each message needs a user/assistant role and text.")
        c = m["content"].strip()
        if not c or len(c) > (4000 if m["role"] == "user" else 10000):
            raise ValueError("Message length out of range.")
        out.append({"role": m["role"], "content": c})
    if out[-1]["role"] != "user":
        raise ValueError("The latest message must be from the user.")
    return out


def _run_agents(orch, text: str) -> Dict[str, Any]:
    lang = detect_lang(text)
    done = threading.Event()
    box: Dict[str, Any] = {}

    def cb(task: Dict[str, Any]) -> None:
        box["t"] = task
        done.set()

    tid = orch.submit(text, lang=lang, on_done=cb)
    finished = done.wait(WAIT_SECONDS)
    t = box.get("t") or {}
    if finished and t.get("summary"):
        s = (t.get("summary_hi") if lang == "hi" and t.get("summary_hi") else t["summary"])
        reply = f"{s}\n\n[Open Control Center](app.html)"
    else:
        reply = (f"NeverMind agents are working on it (task {tid}). "
                 f"[Follow progress in Control Center](app.html)")
    return {"reply": reply, "mode": "agents", "task_id": tid}


def chat(orch, payload: Dict[str, Any]) -> Dict[str, Any]:
    msgs = parse_messages(payload)
    text = msgs[-1]["content"]
    intent = classify_intent(text)
    eng, ok = _engine()
    if intent not in ORCH_INTENT_CHAT_SKIP or not ok:
        return _run_agents(orch, text)
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in msgs[-12:-1])
    user = (f"{convo}\nuser: {text}" if convo else text)
    out = eng.complete(SYSTEM_PROMPT, user, max_tokens=1200, temperature=0.7)
    reply = (out.get("text") or "").strip()
    if not reply:
        raise RuntimeError("The AI provider returned an empty reply.")
    return {"reply": reply, "mode": "llm", "model": eng.model}
