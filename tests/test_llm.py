from types import SimpleNamespace

import pytest

from backend import config
from backend.services import llm


class FakeResponse:
    def __init__(self, text="", parsed=None):
        self.text = text
        self.parsed = parsed


class FakePart:
    @staticmethod
    def from_bytes(data, mime_type):
        return {"data": data, "mime": mime_type}


def enable_fake_sdk(monkeypatch, response=None):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "genai", object())
    monkeypatch.setattr(llm, "types", SimpleNamespace(Part=FakePart))
    monkeypatch.setattr(
        llm, "_generate_strict", lambda *args, **kwargs: response or FakeResponse()
    )


class FakeApiError(Exception):
    def __init__(self, code=None, status=None):
        self.code = code
        self.status = status


def test_no_key_means_unavailable(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert llm.available() is False
    assert llm.parse_request("earbuds under fifty dollars") is None


def test_parse_request_returns_a_dict(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    payload = {"items": [{"name": "earbuds"}], "total_budget": 50}
    monkeypatch.setattr(llm, "_generate", lambda *args, **kwargs: FakeResponse(parsed=payload))

    assert llm.parse_request("earbuds under fifty dollars") == payload


def test_parse_request_accepts_json_text(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm,
        "_generate",
        lambda *args, **kwargs: FakeResponse(text='{"items": [{"name": "charger"}]}'),
    )

    assert llm.parse_request("a charger") == {"items": [{"name": "charger"}]}


def test_parse_request_rejects_invalid_json(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_generate", lambda *args, **kwargs: FakeResponse(text="not json"))

    assert llm.parse_request("earbuds") is None


def test_parse_request_rejects_empty_and_long_text(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_generate", lambda *args, **kwargs: FakeResponse(text='{"items": []}'))

    assert llm.parse_request("   ") is None
    assert llm.parse_request("x" * 501) is None


def test_explain_picks_returns_plain_text(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_generate", lambda *args, **kwargs: FakeResponse(text="Good value."))

    assert llm.explain_picks("earbuds", [{"title": "Nimbus Air Buds"}]) == "Good value."


def test_explain_picks_needs_picks(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    assert llm.explain_picks("earbuds", []) is None


def test_transcribe_audio_returns_text(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_generate", lambda *args, **kwargs: FakeResponse(text="earbuds"))

    assert llm.transcribe_audio(b"wav-bytes", mime_type="audio/wav") == "earbuds"


def test_transcribe_audio_rejects_empty_audio(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    assert llm.transcribe_audio(b"") is None
    assert llm.transcribe_audio(None) is None


def test_checked_transcription_returns_text(monkeypatch):
    enable_fake_sdk(monkeypatch, FakeResponse(text="  wireless earbuds  "))

    assert llm.transcribe_audio_checked(b"wav-bytes") == "wireless earbuds"


def test_checked_transcription_needs_a_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")

    with pytest.raises(llm.LLMError) as exc:
        llm.transcribe_audio_checked(b"wav-bytes")
    assert exc.value.code == "TRANSCRIPTION_UNAVAILABLE"
    assert "configured" in str(exc.value)


def test_checked_transcription_rejects_empty_audio(monkeypatch):
    enable_fake_sdk(monkeypatch)

    for audio in (b"", None, "not-bytes"):
        with pytest.raises(llm.LLMError) as exc:
            llm.transcribe_audio_checked(audio)
        assert exc.value.code == "LLM_BAD_AUDIO"


def test_checked_transcription_treats_a_blank_response_as_empty_speech(monkeypatch):
    enable_fake_sdk(monkeypatch, FakeResponse(text="   "))

    with pytest.raises(llm.LLMError) as exc:
        llm.transcribe_audio_checked(b"wav-bytes")
    assert exc.value.code == "LLM_EMPTY"


def test_checked_transcription_keeps_safe_error_codes(monkeypatch):
    def boom(*args, **kwargs):
        raise llm.LLMError("LLM_RATE_LIMIT")

    enable_fake_sdk(monkeypatch)
    monkeypatch.setattr(llm, "_generate_strict", boom)

    with pytest.raises(llm.LLMError) as exc:
        llm.transcribe_audio_checked(b"wav-bytes")
    assert exc.value.code == "LLM_RATE_LIMIT"
    assert "test-key" not in str(exc.value)


@pytest.mark.parametrize(
    "error,expected",
    [
        (FakeApiError(400), "LLM_BAD_REQUEST"),
        (FakeApiError(401), "LLM_AUTH"),
        (FakeApiError(403), "LLM_AUTH"),
        (FakeApiError(404), "LLM_BAD_MODEL"),
        (FakeApiError(429), "LLM_RATE_LIMIT"),
        (FakeApiError(500), "LLM_SERVER"),
        (FakeApiError(status="UNAUTHENTICATED"), "LLM_AUTH"),
        (FakeApiError(status="NOT_FOUND"), "LLM_BAD_MODEL"),
        (FakeApiError(status="RESOURCE_EXHAUSTED"), "LLM_RATE_LIMIT"),
        (FakeApiError(status="UNAVAILABLE"), "LLM_SERVER"),
        (FakeApiError(status="INVALID_ARGUMENT"), "LLM_BAD_REQUEST"),
    ],
)
def test_gemini_errors_map_to_safe_codes(error, expected):
    assert llm._classify_error(error) == expected


def test_a_plain_exception_is_not_exposed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret=test-key")

    enable_fake_sdk(monkeypatch)
    monkeypatch.setattr(llm, "_generate_strict", boom)

    with pytest.raises(llm.LLMError) as exc:
        llm.transcribe_audio_checked(b"wav-bytes")
    assert exc.value.code == "LLM_FAILED"
    assert "secret" not in str(exc.value)
    assert "test-key" not in str(exc.value)
