from frontend import voice_flow


def test_clip_fingerprint_is_stable_and_non_reversible():
    first = voice_flow.clip_fingerprint(b"audio-bytes")
    second = voice_flow.clip_fingerprint(bytearray(b"audio-bytes"))

    assert first == second
    assert len(first) == 64
    assert "audio-bytes" not in first


def test_empty_audio_has_no_fingerprint():
    assert voice_flow.clip_fingerprint(b"") == ""
    assert voice_flow.clip_fingerprint(None) == ""


def test_transcribe_only_when_the_clip_changes():
    fingerprint = voice_flow.clip_fingerprint(b"clip-1")

    assert voice_flow.should_transcribe(fingerprint, "") is True
    assert voice_flow.should_transcribe(fingerprint, fingerprint) is False
    assert voice_flow.should_transcribe("", fingerprint) is False


def test_known_voice_errors_have_plain_messages():
    assert "did not hear" in voice_flow.transcription_message("NO_SPEECH")
    assert "not configured" in voice_flow.transcription_message(
        "TRANSCRIPTION_UNAVAILABLE"
    )
    assert "could not process" in voice_flow.transcription_message(
        "TRANSCRIPTION_FAILED"
    )


def test_unknown_voice_errors_use_a_safe_fallback():
    assert voice_flow.transcription_message("SOMETHING_NEW", "Try again.") == "Try again."
    assert "Try again" in voice_flow.transcription_message("SOMETHING_NEW", "")
