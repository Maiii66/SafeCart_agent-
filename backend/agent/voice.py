# backend/agent/voice.py
# Voice is an input helper only: it can propose a search and read it back.
# It has no approval or payment path. Audio is transcribed in memory and then dropped.

import io
import wave
from dataclasses import dataclass

from backend import config
from backend.agent import injection, parser


# Voice may do exactly these two things. Everything else is refused.
ALLOWED_ACTIONS = frozenset({"search", "readback"})
FORBIDDEN_ACTIONS = frozenset(
    {"approve", "checkout", "pay", "payment", "purchase", "step_up"}
)


class VoiceSecurityError(PermissionError):
    """Raised when code asks voice to do something it must never do."""


@dataclass(frozen=True)
class AudioClip:
    """Raw audio for one request. It is never written to disk or kept after use."""
    data: bytes
    duration_seconds: float | None = None
    mime_type: str = "audio/wav"


@dataclass(frozen=True)
class VoiceResult:
    """The transcript, the parsed request, and the read-back sentence."""
    ok: bool
    code: str
    reason: str = ""
    transcript: str = ""
    parsed: object = None
    readback: str = ""
    requires_confirmation: bool = True
    action: str = "readback"


_TRANSCRIPTION_REASONS = {
    "LLM_AUTH": "Speech-to-text is not configured correctly.",
    "LLM_BAD_MODEL": "The speech-to-text model is not available.",
    "LLM_BAD_REQUEST": "The speech-to-text service rejected the audio.",
    "LLM_RATE_LIMIT": "Speech-to-text is busy. Try again in a moment.",
    "LLM_SERVER": "Speech-to-text had a server error. Try again.",
    "LLM_TIMEOUT": "Speech-to-text took too long. Try again.",
    "LLM_BAD_AUDIO": "The audio could not be read. Try recording again.",
    "LLM_FAILED": "Speech-to-text could not process the audio.",
}


def _refuse(code, reason, **fields):
    fields.setdefault("requires_confirmation", False)
    return VoiceResult(ok=False, code=code, reason=reason, **fields)


def _safe_transcription_reason(code):
    return _TRANSCRIPTION_REASONS.get(
        str(code or ""), "Speech-to-text could not process the audio."
    )


def estimate_duration_seconds(audio_bytes):
    """Read the length from a WAV header; None means it could not be checked."""
    if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
        return None
    try:
        with wave.open(io.BytesIO(bytes(audio_bytes)), "rb") as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
            if rate <= 0:
                return None
            return frames / float(rate)
    except (wave.Error, EOFError, OSError):
        return None


def build_readback(parsed):
    """Turn the parser result into a clear sentence the person can confirm."""
    text = parser.readback_text(parsed)
    if parsed.items and not parsed.needs_budget:
        return text.rstrip(".") + ". Is that right?"
    return text


def _proposal_from_transcript(transcript):
    """Apply the same input checks as typed text, then build the read-back."""
    if not isinstance(transcript, str) or not transcript.strip():
        return _refuse("NO_SPEECH", "I did not hear any speech.")

    scan = injection.scan_text(transcript)
    if scan.flagged:
        return _refuse(
            "INJECTION_BLOCKED",
            "Voice input failed the safety check: " + ", ".join(scan.reasons),
        )

    try:
        parsed = parser.parse_request(transcript)
    except parser.ParseError as error:
        return _refuse("UNREADABLE", str(error))

    readback = build_readback(parsed)
    fields = {
        "transcript": transcript,
        "parsed": parsed,
        "readback": readback,
    }
    if not parsed.items:
        return _refuse("NO_ITEMS", readback, **fields)
    if parsed.needs_budget:
        return _refuse("NEEDS_BUDGET", readback, **fields)

    return VoiceResult(
        ok=True,
        code="OK",
        reason="search_ready",
        transcript=transcript,
        parsed=parsed,
        readback=readback,
        requires_confirmation=True,
        action="search",
    )


def transcribe_clip(clip, transcribe=None):
    """Check one short clip, transcribe it, and immediately forget the audio bytes."""
    if not isinstance(clip, AudioClip):
        return _refuse("BAD_AUDIO", "Expected an AudioClip.")
    if not config.VOICE_ENABLED:
        return _refuse("VOICE_DISABLED", "Voice input is turned off.")

    data = clip.data
    if not isinstance(data, (bytes, bytearray)) or not data:
        return _refuse("EMPTY_AUDIO", "The audio clip was empty.")

    duration = clip.duration_seconds
    if duration is None:
        duration = estimate_duration_seconds(data)
    if duration is None:
        return _refuse("AUDIO_DURATION_UNKNOWN", "The audio length could not be checked.")

    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return _refuse("AUDIO_DURATION_UNKNOWN", "The audio length could not be checked.")
    if duration < 0:
        return _refuse("AUDIO_DURATION_UNKNOWN", "The audio length could not be checked.")
    if duration > config.MAX_AUDIO_SECONDS:
        return _refuse(
            "AUDIO_TOO_LONG",
            f"Keep clips to {config.MAX_AUDIO_SECONDS} seconds or less.",
        )

    if transcribe is None:
        return _refuse(
            "TRANSCRIPTION_UNAVAILABLE",
            "No speech-to-text service is configured.",
        )

    try:
        transcript = transcribe(bytes(data), mime_type=clip.mime_type)
    except Exception as error:
        code = str(getattr(error, "code", "") or "")
        if code == "TRANSCRIPTION_UNAVAILABLE":
            return _refuse(
                "TRANSCRIPTION_UNAVAILABLE",
                "No speech-to-text service is configured.",
            )
        if code == "LLM_EMPTY":
            return _refuse("NO_SPEECH", "I did not hear any speech.")
        return _refuse("TRANSCRIPTION_FAILED", _safe_transcription_reason(code))
    finally:
        # Drop our reference now. Nothing in this module stores or writes the audio.
        data = b""

    if not isinstance(transcript, str) or not transcript.strip():
        return _refuse("NO_SPEECH", "I did not hear any speech.")
    return _proposal_from_transcript(transcript)


def propose_search(transcript):
    """Build a search proposal from text produced by speech-to-text."""
    return _proposal_from_transcript(transcript)


def confirm_search(proposal, confirmed):
    """Move a confirmed read-back to a search command. It still cannot approve or pay."""
    if not isinstance(proposal, VoiceResult):
        return _refuse("BAD_PROPOSAL", "There is no search to confirm.")
    if not proposal.ok or proposal.action != "search" or proposal.parsed is None:
        return _refuse("BAD_PROPOSAL", "There is no search to confirm.")
    if not confirmed:
        return _refuse(
            "NOT_CONFIRMED",
            "Please confirm the read-back before searching.",
            transcript=proposal.transcript,
            parsed=proposal.parsed,
            readback=proposal.readback,
        )

    return VoiceResult(
        ok=True,
        code="OK",
        reason="search_confirmed",
        transcript=proposal.transcript,
        parsed=proposal.parsed,
        readback=proposal.readback,
        requires_confirmation=False,
        action="search",
    )


def voice_can(action):
    """True only for search and read-back."""
    if not isinstance(action, str):
        return False
    return action.strip().lower() in ALLOWED_ACTIONS


def require_voice_action(action):
    """Refuse loudly if anything tries to give voice a spending action."""
    if not voice_can(action):
        raise VoiceSecurityError(f"Voice cannot perform '{action}'.")
