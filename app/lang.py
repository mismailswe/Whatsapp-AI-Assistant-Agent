"""Whisper may guess any language; we only care about Urdu or English for this app."""
import re

_URDU_SCRIPT = re.compile(r"[\u0600-\u06FF]")
# Roman Urdu / Hindi chat markers (Latin script)
_ROMAN_URDU = re.compile(
    r"\b("
    r"hai|hain|ho|hoon|hun|kaise|kya|kyun|kab|kahan|kidhr|kidhar|main|mein|tum|aap|"
    r"nahi|nahin|acha|theek|han|ji|sunao|batao|karo|kar|rahi|raha|hun|hain|"
    r"kal|aj|abhi|baad|mujhe|tujhe|apko|mera|tera|yeh|woh|kuch|bhi|"
    r"shaadi|college|ghar|nahi|payega|dekhte|zaroori"
    r")\b",
    re.I,
)
# Common English (Latin script) — Roman Urdu rarely uses these patterns
_ENGLISH = re.compile(
    r"\b("
    r"how|what|where|when|why|who|which|are|you|your|is|am|was|were|the|this|that|"
    r"hi|hello|hey|thanks|thank|please|today|tomorrow|yesterday|good|fine|"
    r"doing|going|about|with|from|have|has|had|will|would|can|could|"
    r"message|send|call|late|meeting|ok|okay|yes|no|sure"
    r")\b",
    re.I,
)


def pick_language(info) -> str:
    """info: faster-whisper's TranscriptionInfo (language, all_language_probs)."""
    if info.language in ("ur", "en"):
        return info.language
    probs = dict(getattr(info, "all_language_probs", None) or [])
    if probs.get("ur", 0) >= probs.get("en", 0):
        return "ur"
    return "en"


def detect_incoming_language(text: str) -> str:
    """Urdu script or Roman Urdu → 'ur'; clear English → 'en'."""
    text = (text or "").strip()
    if not text:
        return "en"
    if _URDU_SCRIPT.search(text):
        return "ur"
    ur = len(_ROMAN_URDU.findall(text))
    en = len(_ENGLISH.findall(text))
    if en > ur:
        return "en"
    if ur > en:
        return "ur"
    # Short greetings often English in this project
    low = text.lower()
    if low in {"hi", "hey", "hello", "ok", "okay", "yes", "no", "thanks", "thank you"}:
        return "en"
    return "en"


def reply_looks_urdu(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if _URDU_SCRIPT.search(text):
        return True
    return len(_ROMAN_URDU.findall(text)) >= 1 and len(_ENGLISH.findall(text)) == 0


def reply_looks_english(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if _URDU_SCRIPT.search(text):
        return False
    if _ENGLISH.findall(text):
        return True
    return not reply_looks_urdu(text)


def filter_history_for_match(history: list[dict], incoming_lang: str) -> list[dict]:
    """Drop past assistant replies in the wrong language (common after bad auto-replies)."""
    if not history:
        return history
    out: list[dict] = []
    i = 0
    while i < len(history):
        msg = history[i]
        if msg.get("role") != "user":
            out.append(msg)
            i += 1
            continue
        user_lang = detect_incoming_language(msg.get("content", ""))
        if i + 1 < len(history) and history[i + 1].get("role") == "assistant":
            assistant = history[i + 1].get("content", "")
            if incoming_lang == "en" and user_lang == "en" and reply_looks_urdu(assistant):
                i += 2
                continue
            if incoming_lang == "ur" and user_lang == "ur" and reply_looks_english(assistant):
                i += 2
                continue
            out.append(msg)
            out.append(history[i + 1])
            i += 2
            continue
        out.append(msg)
        i += 1
    return out
