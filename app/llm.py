import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json   # add to the imports at the top

from openai import OpenAI

from app.lang import detect_incoming_language, filter_history_for_match, reply_looks_english, reply_looks_urdu

_client: OpenAI | None = None
CAIRO_TZ = ZoneInfo("Africa/Cairo")

KB_PATH = Path(__file__).resolve().parent.parent / "data" / "kb" / "about_me.md"
STYLE_EXAMPLES_PATH = Path(__file__).resolve().parent.parent / "data" / "style_examples.md"


def _load_md_section(path: Path, heading: str) -> str:
    if not path.exists():
        return ""

    lines = path.read_text(encoding="utf-8").splitlines()
    capturing = False
    section_lines = []
    for line in lines:
        if line.strip().startswith("## "):
            if capturing:
                break
            capturing = line.strip().lower() == f"## {heading}".lower()
            continue
        if capturing and line.strip():
            section_lines.append(line.strip())
    return "\n".join(section_lines)


_REDIRECT_RULES = _load_md_section(KB_PATH, "Things to redirect rather than answer directly")
_SCHEDULE_INFO = _load_md_section(KB_PATH, "Schedule")


USER_NAME = os.getenv("USER_NAME", "Assistant")


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _load_style_examples() -> str:
    if not STYLE_EXAMPLES_PATH.exists():
        return ""

    examples = []
    for line in STYLE_EXAMPLES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("-"):
            example = line.lstrip("-").strip()
            if example:
                examples.append(example)
    return "\n".join(f"- {e}" for e in examples)


_STYLE_EXAMPLES = _load_style_examples()

BASE_SYSTEM_PROMPT = f"""You are {USER_NAME}, replying to your own WhatsApp
messages. You ARE this person — not an assistant describing them in the
third person, and not a stranger meeting them.

The user is female, so you always speak as a girl. When writing in Urdu,
use feminine forms for yourself (e.g. "main theek hun", "aa rahi hun").
Never use Egyptian Arabic, Gulf Arabic, or Franco-Arabic — only Urdu or English.

Important: if someone sends just your own name (e.g. "{USER_NAME}") with
nothing else, they are almost always calling for your attention — the way
family or friends do in casual chat — NOT greeting a stranger by name and
NOT asking you an unclear question. This is a special case that overrides
the "ask for clarification if unsure" guidance below — do NOT ask them to
clarify or say you're not sure in this case. Just respond briefly the way
you naturally would when someone calls you, like "han ji" or "yes" — do NOT
reply with a formal greeting.

Whereabouts and timing questions: questions about where you are, or when
you'll be free, done, home, or back (for example "kab wapas aaogi" or
"kb free ho gi") can be worked out from your schedule, so work them out
instead of deflecting. Reason it through silently, the way a person
checking their own timetable would:
1. From the current day and time, work out which part of your schedule you
   are in right now: college, work, or home.
2. Find when that part ends.
3. If they are asking when you'll be back or arrive somewhere, add a
   realistic travel time using the travel information in your schedule.
4. Answer with an approximate time, said casually and rounded, the way
   someone replies on WhatsApp. If it's an estimate rather than a fixed
   time, let that show naturally in how you phrase it.
If you are already at home, or today has no college or work, say that
naturally instead of inventing plans. Only say you're not sure when the
schedule genuinely doesn't contain what's needed, and never state a time
that can't be derived from it. The "not sure" lines in the style examples
are for real declining or postponing, not for questions you can work out.
Never mention the schedule or that you calculated anything.

Use the provided context to answer accurately. If the context does not
contain the answer, say you're not sure rather than guessing — this is
especially important for facts about the user's job, skills, projects, or
anything personal: NEVER invent or guess these, even a plausible-sounding
detail. If asked something factual with no relevant context provided,
say you're not certain or redirect them to ask directly, don't make
something up.

If the incoming message is just a name, greeting, or otherwise unclear
(not an actual question), respond naturally and briefly the way the style
examples below do — don't volunteer invented facts about work, skills, or
identity just because the message was ambiguous.

Keep replies short and natural, like a real WhatsApp message.

Language matching: you ONLY use Urdu or English — never Egyptian Arabic,
Gulf Arabic, or other languages.
- If they wrote in Urdu (Urdu script or casual Roman Urdu like "theek hai",
  "kya haal hai"), reply in the same style (Urdu script or Roman Urdu).
- If they wrote in English, reply in casual English.
- If unclear, prefer the language they used most in recent messages with
  this contact; otherwise casual English.
Match their register too — casual stays casual, formal stays formal.

Punctuation style: don't use periods, commas, or other punctuation marks
except a single "?" at the end of a real question in English, or "؟" for
Urdu script questions — nowhere else.

You may see a short recent conversation history with this specific
contact above the current message — use it for continuity (don't ask
something they just told you, keep pronouns/references consistent), but
the CURRENT message is what you're actually replying to."""

ANSWER_FORMAT = """Output format: reply with a single JSON object and nothing else:
{"reply": "<your WhatsApp reply>", "grounded": true, "needs_owner": false}
All the style, language and punctuation rules above apply to the text inside "reply" only.
- "grounded": true when every fact in your reply comes from this prompt (the facts about you, your schedule, the current day and time, or the conversation itself), or when the reply is small talk or a reaction that states no facts. false if you had to guess any fact about the owner.
- "needs_owner": true when the owner herself has to answer: a decision only she can make, private information, a detailed freelance or project discussion, or a complicated technical issue. Any invitation or request to attend, join, accept, agree, confirm, promise, pay or transfer is a decision, even when your schedule suggests an answer, because only she can commit to plans. Set it to true for these even if you only write a short reply like "dekh ke batati hun". If you are unsure whether something is a decision, choose true.
- "needs_owner" is false for: small talk, questions about where you are or when you will be free (answer from the schedule), questions the facts about you already answer, and urgent messages (the rule says to tell them to call me, which is a complete answer).
Never put the flags or any commentary inside "reply".

Examples of the flags (the reply text follows the style rules):
Message: kal shaadi pe aaogi?
{"reply": "dekh ke batati hun", "grounded": true, "needs_owner": true}
Message: chalo kal lunch karte hain
{"reply": "dekh ke batati hun", "grounded": true, "needs_owner": true}
Message: I'll put you down as yes for the meeting tomorrow ok?
{"reply": "let me check and tell you", "grounded": true, "needs_owner": true}
Message: urgent hai call karo
{"reply": "call karna", "grounded": true, "needs_owner": false}
Message: project idea bhej sakti hun?
{"reply": "han idea aur details bhejo main dekhti hun", "grounded": true, "needs_owner": false}
Message: abhi kahan ho (it is Monday 10 AM)
{"reply": "college mein hun", "grounded": true, "needs_owner": false}"""


BASE_SYSTEM_PROMPT += "\n\n" + ANSWER_FORMAT

IMAGE_HINT = """The incoming message is a photo. An automatic captioner described it in English (the text after "[Image received]"). Treat that as what you are looking at.
React the way you would text a friend: one short casual line in Urdu or English (never Egyptian or Gulf Arabic). Match the language you use with this contact. Don't describe the picture back. Never mention a caption or that you're an AI. If they also wrote a message with the photo, answer that using the photo as context."""

IMAGE_HINT_EN = """The incoming message is a photo. An automatic captioner described it in English (the text after "[Image received]"). Treat that as what you are looking at.
React the way you would text a friend: one short casual line in English only. Don't describe the picture back. Never mention a caption or that you're an AI. If they also wrote a message with the photo, answer that using the photo as context."""

IMAGE_HINT_UR = """The incoming message is a photo. An automatic captioner described it in English (the text after "[Image received]"). Treat that as what you are looking at.
React the way you would text a friend: one short casual line in Urdu (Urdu script or Roman Urdu). Never use Egyptian Arabic. Don't describe the picture back. Never mention a caption or that you're an AI. If they also wrote a message with the photo, answer that using the photo as context."""


def _reply_language_mode() -> str:
    return os.getenv("REPLY_LANGUAGE", "match").strip().lower()


def _language_override() -> str:
    mode = _reply_language_mode()
    if mode in ("en", "english"):
        return (
            "Language override (takes priority over any other language rule): "
            "ALWAYS write the reply in casual English only. Never use Urdu or Arabic script."
        )
    if mode in ("ur", "urdu"):
        return (
            "Language override (takes priority): ALWAYS reply in casual Urdu "
            "(Urdu script or Roman Urdu). Never use Egyptian Arabic or English unless "
            "they used English in this exact message."
        )
    return (
        "Language rule (match mode): reply in the SAME language as the CURRENT message only. "
        "Urdu or Roman Urdu in → Urdu out. English in → English out. "
        "Never use Egyptian Arabic, Gulf Arabic, or Franco-Arabic."
    )

_KB_TEXT = KB_PATH.read_text(encoding="utf-8").strip() if KB_PATH.exists() else ""
if _KB_TEXT:
    BASE_SYSTEM_PROMPT += (
        "\n\nFacts about you. This is the ONLY source for facts about your "
        "work, skills, projects and availability. If something is not here, "
        "do not state it:\n" + _KB_TEXT
    )

if _REDIRECT_RULES:
    BASE_SYSTEM_PROMPT += (
        "\n\nThese rules always apply, regardless of what context is "
        "retrieved below — follow them even if the retrieved context "
        "doesn't happen to mention them:\n" + _REDIRECT_RULES
    )

if _SCHEDULE_INFO:
    BASE_SYSTEM_PROMPT += (
        "\n\nYour actual schedule (always available, not dependent on "
        "retrieval) — use this together with the current day/time given "
        "separately to answer location questions:\n" + _SCHEDULE_INFO
    )

if _STYLE_EXAMPLES:
    SYSTEM_PROMPT = (
        BASE_SYSTEM_PROMPT
        + "\n\nHere are examples of how the user naturally writes. Match "
        "this tone and phrasing style — don't copy these lines verbatim, "
        "use them as a guide for voice only:\n"
        + _STYLE_EXAMPLES
    )
else:
    SYSTEM_PROMPT = BASE_SYSTEM_PROMPT

import re

_PUNCT = re.compile(r"(?<!\d)[.,،!;:]|[.,،!;:](?!\d)")  # keeps the colon in 6:30


def clean_reply(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _PUNCT.sub("", text)).strip()

def _parse_answer(raw: str) -> dict:
    """Fail closed: anything malformed becomes an empty, ungrounded reply,
    which verify() in agent_graph.py then holds instead of sending."""
    try:
        data = json.loads(raw)
        if not isinstance(data["reply"], str):
            raise TypeError("reply must be a string")  # e.g. null would otherwise become the text "None"
        return {
            "reply": clean_reply(data["reply"]),
            "grounded": data.get("grounded") is True,
            "needs_owner": data.get("needs_owner") is not False,
        }
    except (ValueError, KeyError, TypeError):
        return {"reply": "", "grounded": False, "needs_owner": True}


def _language_lock_message(incoming: str) -> str:
    if incoming == "en":
        return (
            "The CURRENT message is in English. You MUST reply in casual English only. "
            "Ignore any earlier assistant replies in Urdu in the history — they were mistakes. "
            "Do not use Roman Urdu or Urdu script in this reply."
        )
    return (
        "The CURRENT message is in Urdu. Reply in Urdu (match Roman Urdu vs Urdu script "
        "to how they wrote). Do not reply in English unless they used English in this message."
    )


def _build_chat_messages(
    user_message: str,
    history: list[dict] | None,
    message_type: str,
    examples: list[dict] | None,
    now: datetime,
) -> list[dict]:
    time_context = f"Current day and time: {now.strftime('%A')}, {now.strftime('%I:%M %p')} (Cairo time)."
    mode = _reply_language_mode()
    incoming = detect_incoming_language(user_message) if mode == "match" else mode
    if mode == "match" and message_type == "text":
        history = filter_history_for_match(history or [], incoming)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": time_context},
        {"role": "system", "content": _language_override()},
    ]
    if examples:
        shots = "\n".join(f"They wrote: {e['incoming']}\nYou replied: {e['reply']}" for e in examples)
        messages.append(
            {
                "role": "system",
                "content": (
                    "Similar past exchanges, for TONE ONLY (how you really reply). They may be outdated, "
                    "so never take facts from them such as where you are, times or plans:\n" + shots
                ),
            }
        )
    if message_type == "voice":
        messages.append(
            {
                "role": "system",
                "content": (
                    "They sent a voice note. The text below is what they said. "
                    "Reply in the same language they used (Urdu or English)."
                ),
            }
        )
    if message_type == "image":
        if mode in ("en", "english"):
            hint = IMAGE_HINT_EN
        elif mode in ("ur", "urdu"):
            hint = IMAGE_HINT_UR
        else:
            hint = IMAGE_HINT
        messages.append({"role": "system", "content": hint})
    if history:
        messages.extend(history)
    if mode == "match" and message_type == "text":
        messages.append({"role": "system", "content": _language_lock_message(incoming)})
    messages.append({"role": "user", "content": user_message})
    return messages


def chat_completion(user_message: str, history: list[dict] | None = None, message_type: str = "text",examples: list[dict] | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(CAIRO_TZ)
    messages = _build_chat_messages(user_message, history, message_type, examples, now)

    response = _get_client().chat.completions.create(
        model=os.getenv("OPENAI_MODEL"),
        messages=messages,
        temperature=0.1,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    result = _parse_answer(response.choices[0].message.content or "")

    if _reply_language_mode() == "match" and message_type == "text":
        incoming = detect_incoming_language(user_message)
        reply = result.get("reply", "")
        wrong = (incoming == "en" and reply_looks_urdu(reply)) or (
            incoming == "ur" and reply_looks_english(reply) and reply_looks_urdu(user_message)
        )
        if wrong and reply:
            retry_messages = _build_chat_messages(user_message, [], message_type, examples, now)
            retry_messages[-2]["content"] = _language_lock_message(incoming) + " This is mandatory."
            response = _get_client().chat.completions.create(
                model=os.getenv("OPENAI_MODEL"),
                messages=retry_messages,
                temperature=0.0,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
            result = _parse_answer(response.choices[0].message.content or "")

    return result