# scripts/try_voice.py
# Quick manual check of speech-to-text. Audio is read once and never stored.

import sys

from backend.services import llm


def main(argv=None, transcribe=None, say=print):
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        say("Usage: python -m scripts.try_voice <path-to-audio.wav>")
        return 1

    path = argv[0]
    try:
        with open(path, "rb") as handle:
            audio_bytes = handle.read()
    except OSError:
        say("Could not read that audio file.")
        return 1

    if not audio_bytes:
        say("That audio file is empty.")
        return 1

    transcribe = transcribe or llm.transcribe_audio_checked
    try:
        transcript = transcribe(audio_bytes, mime_type="audio/wav")
    except Exception as error:
        code = str(getattr(error, "code", "") or "")
        if code:
            say(f"Transcription failed ({code}): {llm.safe_message(code)}")
        else:
            say("Transcription failed. Check the Gemini key and model settings.")
        return 1

    text = transcript.strip() if isinstance(transcript, str) else ""
    if not text:
        say("No speech was detected in that clip.")
        return 1
    say(f'Transcript: "{text}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
