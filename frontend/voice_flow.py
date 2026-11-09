from __future__ import annotations

import hashlib

VOICE_MESSAGES = {
    "NO_SPEECH": "I did not hear any speech. Try again and speak clearly.",
    "TRANSCRIPTION_UNAVAILABLE": (
        "Transcription is not configured in this environment. "
        "Type your request in the Shop tab instead."
    ),
    "TRANSCRIPTION_FAILED": "Speech-to-text could not process that clip. Try again.",
    "EMPTY_AUDIO": "The recording was empty. Try again.",
    "BAD_AUDIO": "The recording could not be read. Try again.",
    "AUDIO_DURATION_UNKNOWN": "The recording length could not be checked. Try again.",
    "AUDIO_TOO_LONG": "Keep recordings to 15 seconds or less.",
    "VOICE_DISABLED": "Voice input is turned off.",
    "INJECTION_BLOCKED": "Voice input failed the safety check. Please rephrase your request.",
    "NEEDS_BUDGET": "Please include a budget in your request.",
    "NO_ITEMS": "I did not hear any items to search for.",
    "UNREADABLE": "I could not understand that request. Please try again.",
    "NOT_CONFIRMED": "Confirm the read-back before searching.",
    "BAD_PROPOSAL": "There is no search to confirm.",
}


def clip_fingerprint(data) -> str:
    """A stable, non-reversible fingerprint for one audio clip."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return ""
    return hashlib.sha256(bytes(data)).hexdigest()


def should_transcribe(current_fingerprint, last_fingerprint) -> bool:
    """Transcribe only when there is a clip and it differs from the last one handled."""
    current = str(current_fingerprint or "")
    return bool(current) and current != str(last_fingerprint or "")


def transcription_message(code, fallback="") -> str:
    key = str(code or "")
    if key in VOICE_MESSAGES:
        return VOICE_MESSAGES[key]
    text = str(fallback or "").strip()
    return text or "The voice request could not be processed. Try again."
