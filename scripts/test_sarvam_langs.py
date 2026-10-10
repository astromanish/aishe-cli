#!/usr/bin/env python3
"""Aishe — Sarvam TTS+STT round-trip, top-10 Indian languages by speakers.

For each language: synth the phrase with Sarvam /text-to-speech
(target_language_code), save the WAV, then transcribe it back with
/speech-to-text (language_code). Emits one line per language; used by
`aishe doctor --voice / region coverage`.
"""
import base64, io, json, sys, time
import requests

API = "https://api.sarvam.ai"
LANGS = json.load(open("/tmp/sarvam_langs.json"))["langs"]
KEY = giud = None


def main():
    import yaml, pathlib
    c = yaml.safe_load((pathlib.Path.home() / ".config/aishe/config.yaml").read_text())
    key = ((c.get("providers") or {}).get("sarvam") or {}).get("api_key", "")
    if not key:
        print(json.dumps({"error": "no sarvam key"})); sys.exit(1)

    TTS_MODEL = "bulbul:v3"
    STT_MODEL = "saarika:v2.5"
    SPEAKER = "priya"  # F4 alias
    rows = []
    for name, code, phrase in LANGS:
        row = {"language": name, "code": code}
        # ── TTS ──
        t0 = time.time()
        try:
            r = requests.post(f"{API}/text-to-speech",
                              headers={"api-subscription-key": key, "Content-Type": "application/json"},
                              json={"text": phrase, "target_language_code": code,
                                    "speaker": SPEAKER, "model": TTS_MODEL},
                              timeout=120)
            if r.status_code != 200:
                row["tts"] = f"HTTP {r.status_code}: {r.text[:200]}"
                rows.append(row); continue
            audios = (r.json() or {}).get("audios") or []
            if not audios:
                row["tts"] = "no audio"; rows.append(row); continue
            wav = base64.b64decode(audios[0])
            row["tts"] = "ok"
            row["tts_ms"] = round((time.time() - t0) * 1000)
            row["wav_bytes"] = len(wav)
        except Exception as e:
            row["tts"] = f"err {e}"; rows.append(row); continue

        # ── STT on that audio ──
        t0 = time.time()
        try:
            import tempfile
            suf = ".wav"
            with tempfile.NamedTemporaryFile(suffix=suf, delete=False) as t:
                t.write(wav); p = t.name
            with open(p, "rb") as fh:
                resp = requests.post(f"{API}/speech-to-text",
                                     headers={"api-subscription-key": key},
                                     files={"file": ("a.wav", fh, "audio/wav")},
                                     data={"model": STT_MODEL, "language_code": code},
                                     timeout=120)
            transcript = (resp.json() or {}).get("transcript", "") if resp.status_code == 200 else \
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            row["stt"] = transcript if isinstance(transcript, str) and transcript else "(empty)"
            row["stt_ms"] = round((time.time() - t0) * 1000)
            row["roundtrip"] = "PASS" if transcript and not str(transcript).startswith("HTTP") else "FAIL"
        except Exception as e:
            row["stt"] = f"err {e}"
            row["roundtrip"] = "FAIL"
        rows.append(row)

    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()