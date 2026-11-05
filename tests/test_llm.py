from backend import config
from backend.services import llm


class FakeResponse:
    def __init__(self, text="", parsed=None):
        self.text = text
        self.parsed = parsed


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
