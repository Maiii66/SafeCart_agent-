from backend.services import llm
from scripts import try_voice


def run(argv, transcribe=None):
    lines = []
    code = try_voice.main(argv, transcribe=transcribe, say=lines.append)
    return code, lines


def test_try_voice_prints_the_transcript(tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"wav-bytes")

    def transcribe(data, mime_type="audio/wav"):
        assert data == b"wav-bytes"
        assert mime_type == "audio/wav"
        return "earbuds under fifty dollars"

    code, lines = run([str(audio)], transcribe)
    assert code == 0
    assert 'Transcript: "earbuds under fifty dollars"' in lines


def test_try_voice_reports_a_safe_failure_code(tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"wav-bytes")

    def transcribe(data, mime_type="audio/wav"):
        raise llm.LLMError("LLM_AUTH")

    code, lines = run([str(audio)], transcribe)
    assert code == 1
    output = "\n".join(lines)
    assert "LLM_AUTH" in output
    assert "API key was rejected" in output
    assert "test-key" not in output


def test_try_voice_reports_empty_audio_file(tmp_path):
    audio = tmp_path / "empty.wav"
    audio.write_bytes(b"")

    code, lines = run([str(audio)])
    assert code == 1
    assert any("empty" in line.lower() for line in lines)


def test_try_voice_requires_a_file():
    code, lines = run([])
    assert code == 1
    assert any("Usage" in line for line in lines)


def test_try_voice_handles_a_missing_file(tmp_path):
    code, lines = run([str(tmp_path / "missing.wav")])
    assert code == 1
    assert any("Could not read" in line for line in lines)


def test_try_voice_handles_an_empty_transcript(tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"wav-bytes")
    code, lines = run([str(audio)], lambda *args, **kwargs: "   ")
    assert code == 1
    assert any("No speech" in line for line in lines)
