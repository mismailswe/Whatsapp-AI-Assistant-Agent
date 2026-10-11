"""Text-to-speech with edge-tts (free, no API key, needs internet).

edge_tts is imported lazily so the rest of the app (and the tests) don't need it.
"""
import asyncio
import base64
import os
import re
import shutil
import subprocess
import tempfile

from app.log import log_event

DEFAULT_VOICE = "en-US-AriaNeural"
MAX_SPEECH_CHARS = 500   # keeps a spoken reply short and fast to generate
# WhatsApp PTT expects mono Opus in OGG, not MP3.
WHATSAPP_VOICE_MIME = "audio/ogg; codecs=opus"

URDU_STYLE = (
    "Speak in natural casual Urdu like a friendly young Pakistani woman on WhatsApp. "
    "If the text is English, speak clear natural English."
)
_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")
_LATIN = re.compile(r"[A-Za-z]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]")


def _reply_lang() -> str:
    return os.getenv("REPLY_LANGUAGE", "match").strip().lower()


def pick_voice(text: str) -> str:
    """Urdu voice for Urdu script / Roman Urdu; English voice otherwise."""
    urdu = os.getenv("DASHBOARD_VOICE_UR", os.getenv("DASHBOARD_VOICE_AR", "ur-PK-UzmaNeural"))
    english = os.getenv("DASHBOARD_VOICE_EN", DEFAULT_VOICE)
    mode = _reply_lang()
    if mode in ("en", "english"):
        return english
    if mode in ("ur", "urdu"):
        return urdu
    return urdu if len(_ARABIC_SCRIPT.findall(text)) > len(_LATIN.findall(text)) else english


def clean_for_speech(text: str) -> str:
    """Drop emoji and markdown symbols the voice would read out, and cap the length."""
    text = _EMOJI.sub(" ", text).replace("\u0640", "")   # tatweel (ـ) is only for looks
    text = re.sub(r"[*_`#>~|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPEECH_CHARS:
        cut = text[:MAX_SPEECH_CHARS]
        text = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return text


def _openai_speech(text: str) -> bytes:
    from app.llm import _get_client   # lazy: same OpenAI key as the chat model

    style = os.getenv("OPENAI_TTS_STYLE", "")
    if not style:
        mode = _reply_lang()
        urdu_text = len(_ARABIC_SCRIPT.findall(text)) > len(_LATIN.findall(text))
        if mode in ("en", "english") or (mode == "match" and not urdu_text):
            style = "Speak in clear, casual natural English like a friendly young woman on WhatsApp."
        else:
            style = URDU_STYLE
    kwargs = dict(
        model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.getenv("OPENAI_TTS_VOICE", "coral"),
        input=text,
        response_format="mp3",
    )
    try:
        resp = _get_client().audio.speech.create(**kwargs, instructions=style)
    except TypeError:
        resp = _get_client().audio.speech.create(**kwargs)
    return resp.content


def provider() -> str:
    return os.getenv("TTS_PROVIDER", "edge").strip().lower()


async def synthesize_bytes(text: str, voice: str | None = None) -> bytes:
    """MP3 bytes for the dashboard player. TTS_PROVIDER=openai uses an instructable
    voice (Egyptian accent); if it fails the free edge voice is used instead."""
    import edge_tts

    text = clean_for_speech(text)
    if not text:
        raise ValueError("nothing to say")
    if provider() == "openai":
        try:
            return await asyncio.to_thread(_openai_speech, text)
        except Exception as err:   # no key, quota, or an old openai package: fall back
            log_event("openai_tts_failed", level="warning", error=type(err).__name__)
    chunks = []
    async for chunk in edge_tts.Communicate(text, voice or pick_voice(text)).stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("no audio returned")
    return audio


def _to_whatsapp_voice_ogg(audio: bytes, input_suffix: str) -> bytes:
    """Re-encode to Opus OGG so phones can download voice notes from the bridge."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (needed for WhatsApp voice replies)")

    with tempfile.NamedTemporaryFile(suffix=input_suffix, delete=False) as fin:
        fin.write(audio)
        inp = fin.name
    out = f"{inp}.ptt.ogg"
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                inp,
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "libopus",
                "-b:a",
                "64k",
                "-application",
                "voip",
                out,
            ],
            capture_output=True,
            timeout=90,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")[:400]
            raise RuntimeError(err or "ffmpeg failed")
        return open(out, "rb").read()
    finally:
        for path in (inp, out):
            try:
                os.remove(path)
            except OSError:
                pass


def _whatsapp_voice_b64(audio: bytes, input_suffix: str) -> tuple[str, str]:
    ogg = _to_whatsapp_voice_ogg(audio, input_suffix)
    return base64.b64encode(ogg).decode("utf-8"), WHATSAPP_VOICE_MIME


async def synthesize_to_base64(text: str, voice: str | None = None) -> tuple[str, str]:
    """For WhatsApp voice replies (REPLY_WITH_VOICE). Returns (base64 audio, mimetype)."""
    import edge_tts

    spoken = clean_for_speech(text)
    if not spoken:
        raise ValueError("nothing to say")
    voice = voice or pick_voice(spoken)

    if provider() == "openai":
        mp3 = await asyncio.to_thread(_openai_speech, spoken)
        return await asyncio.to_thread(_whatsapp_voice_b64, mp3, ".mp3")

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        path = f.name
    try:
        await edge_tts.Communicate(spoken, voice).save(path)
        with open(path, "rb") as audio:
            raw = audio.read()
        return await asyncio.to_thread(_whatsapp_voice_b64, raw, ".ogg")
    except Exception as err:
        log_event("edge_tts_failed", level="warning", error=type(err).__name__)
        try:
            mp3 = await asyncio.to_thread(_openai_speech, spoken)
            return await asyncio.to_thread(_whatsapp_voice_b64, mp3, ".mp3")
        except Exception as openai_err:
            log_event("openai_tts_failed", level="warning", error=type(openai_err).__name__)
            raise err from openai_err
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
