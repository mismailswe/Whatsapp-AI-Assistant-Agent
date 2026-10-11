"""Speech-to-text using faster-whisper. Runs locally, no API key needed."""
import base64
import os
import tempfile

from app.lang import pick_language

_models: dict[str, object] = {}

# Whisper often hallucinates these on silence / unreadable WhatsApp notes.
_WHISPER_NO_SPEECH = frozenset(
    {
        "you",
        "thank you",
        "thanks",
        "thanks for watching",
        "bye",
        "subtitle",
        ".",
        "...",
    }
)


def transcription_is_empty(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    return cleaned.lower() in _WHISPER_NO_SPEECH


def _get_model(size: str | None = None):
    """One cached model per size, so WhatsApp voice notes can use 'small' while
    the dashboard uses a bigger one (DASHBOARD_WHISPER_SIZE=medium)."""
    from faster_whisper import WhisperModel

    size = size or os.getenv("WHISPER_MODEL_SIZE", "small")
    if size not in _models:
        _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _models[size]


def transcribe_bytes(
    audio: bytes,
    language: str = "ar",
    model_size: str | None = None,
    vad: bool = False,
    suffix: str = ".audio",
) -> str:
    # delete=False + close before use: Windows can't reopen a temp file
    # that is still open in this process. Use .ogg for WhatsApp voice notes.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio)
        path = f.name

    try:
        model = _get_model(model_size)
        # VAD + language=auto can raise ValueError on short/quiet WhatsApp .ogg notes.
        lang_attempts: list[str | None] = []
        if language == "auto":
            lang_attempts = [None, "en", "ur"]
        else:
            lang_attempts = [language]
        vad_attempts = [vad] if not vad else [True, False]

        for use_vad in vad_attempts:
            for lang in lang_attempts:
                try:
                    segments, info = model.transcribe(
                        path,
                        language=lang,
                        vad_filter=use_vad,
                    )
                except ValueError:
                    continue
                if lang is None:
                    chosen = pick_language(info)
                    if chosen != info.language:
                        try:
                            segments, info = model.transcribe(
                                path, language=chosen, vad_filter=use_vad
                            )
                        except ValueError:
                            continue
                text = " ".join(seg.text.strip() for seg in segments).strip()
                if text:
                    return text
        return ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def transcribe_base64_audio(media_base64: str) -> str:
    audio = base64.b64decode(media_base64)
    pref = os.getenv("REPLY_LANGUAGE", "match").strip().lower()
    opts = (".ogg", True)
    if pref in ("en", "english"):
        for lang in ("en", "auto"):
            text = transcribe_bytes(audio, language=lang, vad=opts[1], suffix=opts[0])
            if text.strip():
                return text
        return ""
    if pref in ("ur", "urdu"):
        for lang in ("ur", "auto"):
            text = transcribe_bytes(audio, language=lang, vad=opts[1], suffix=opts[0])
            if text.strip():
                return text
        return ""
    text = transcribe_bytes(audio, language="auto", vad=opts[1], suffix=opts[0])
    return text
