#!/usr/bin/env python3
"""Aishe — Sarvam TTS+STT coverage, all 23 supported Indian language codes.

Round-trip each code: TTS(phrase) -> wav -> STT(wav) -> transcript.
Emits JSON with per-language tts/stt status + confirmation audio bytes.
"""
import base64, json, sys, time, io, tempfile, pathlib
import yaml, requests

API = "https://api.sarvam.ai"
LANG_NAMES = {
    "as-IN":"Assamese","bn-IN":"Bengali","brx-IN":"Bodo","doi-IN":"Dogri",
    "en-IN":"English (Indian)","gu-IN":"Gujarati","hi-IN":"Hindi","kn-IN":"Kannada",
    "kok-IN":"Konkani","ks-IN":"Kashmiri","mai-IN":"Maithili","ml-IN":"Malayalam",
    "mni-IN":"Manipuri","mr-IN":"Marathi","ne-IN":"Nepali","od-IN":"Odia",
    "pa-IN":"Punjabi","sa-IN":"Sanskrit","sat-IN":"Santali","sd-IN":"Sindhi",
    "ta-IN":"Tamil","te-IN":"Telugu","ur-IN":"Urdu",
}
PHRASES = {
    "hi-IN":"नमस्कार, मैं ऐशे हूँ और आपकी मदद के लिए यहाँ हूँ।",
    "bn-IN":"নমস্কার, আমি ঐশে, আপনার সাহায্যে আছি।",
    "mr-IN":"नमस्कार, मी ऐशे आणि मदतीसाठी येथे आहे।",
    "te-IN":"నమస్కారం, నేను ఐషే, సహాయం కోసం ఇక్కడ ఉన్నాను.",
    "ta-IN":"வணக்கம், நான் ஐஷே, உதவிக்கு இங்கே உள்ளேன்.",
    "gu-IN":"નમસ્તે, હું ઐશે અને મદદ માટે અહીં છું.",
    "kn-IN":"ನಮಸ್ಕಾರ, ನಾನು ಐಷೇ, ಸಹಾಯಕ್ಕಾಗಿ ಇಲ್ಲಿದ್ದೇನೆ.",
    "ml-IN":"നമസ്കാരം, ഞാൻ ഐഷേ, സഹായത്തിന് ഇവിടെ ഉണ്ട്.",
    "od-IN":"ନମସ୍କାର, ମୁଁ ଐଶେ ଏବଂ ସାହାଯ୍ୟ ପାଇଁ ଏଠାରେ ଅଛି।",
    "pa-IN":"ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਮੈਂ ਐਸ਼ੇ, ਮਦਦ ਲਈ ਇੱਥੇ ਹਾਂ।",
    "as-IN":"নমস্কাৰ, মই এইচ, সহায়ৰ বাবে ইয়াত আছো।",
    "ur-IN":"السلام علیکم، میں عیشے اور مدد کے لیے حاضر ہوں۔",
    "ne-IN":"नमस्ते, म ऐशे र मद्दतका लागि यहाँ छु।",
    "sa-IN":"नमस्ते, अहं ऐशे, सहायतायै अत्र अस्मि।",
    "ks-IN":"اسی سلام، مے عیشے تہ مدد خاطر ییٖہ چھُس۔",
    "mai-IN":"नमस्कार, हम ऐशे छी आओर सहायता करवाक लेल अतए छी।",
    "kok-IN":"नमस्कार, हांव ऐशे आनी मदतीक खाती हांगा आसां।",
    "brx-IN":"खुलुमबाद, आं ऐशे आर मदद जानां।",
    "doi-IN":"नमस्कार, मैं ऐशे आर सहायता रै।",
    "mni-IN":"খুরুমজারি, ঐশে ঐলী।",
    "sat-IN":"জোহার, আইশে আৰে সহায়।",
    "sd-IN":"سهر جو آدعيس، مان عيشे آهيان.",
}
SPEAKER = "priya"
TTS_MODEL, STT_MODEL = "bulbul:v3", "saarika:v2.5"


def main():
    c = yaml.safe_load((pathlib.Path.home() / ".config/aishe/config.yaml").read_text())
    key = ((c.get("providers") or {}).get("sarvam") or {}).get("api_key", "")
    if not key:
        sys.exit("no sarvam api key")

    rows = []
    for code in sorted(LANG_NAMES):
        row = {"code": code, "language": LANG_NAMES[code]}
        phrase = PHRASES.get(code, "नमस्कार।")
        t0 = time.time()
        try:
            r = requests.post(f"{API}/text-to-speech",
                              headers={"api-subscription-key": key, "Content-Type": "application/json"},
                              json={"text": phrase, "target_language_code": code,
                                    "speaker": SPEAKER, "model": TTS_MODEL}, timeout=120)
            if r.status_code != 200:
                row["tts"] = f"HTTP {r.status_code}"
                rows.append(row); continue
            audios = (r.json() or {}).get("audios") or []
            if not audios:
                row["tts"] = "no audio"; rows.append(row); continue
            wav = base64.b64decode(audios[0])
            row["tts"] = "ok"; row["tts_ms"] = round((time.time() - t0) * 1000)
            row["wav_kb"] = round(len(wav) / 1024)
        except Exception as e:
            row["tts"] = f"err {e}"; rows.append(row); continue

        t0 = time.time()
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
                t.write(wav); p = t.name
            with open(p, "rb") as fh:
                resp = requests.post(f"{API}/speech-to-text",
                                     headers={"api-subscription-key": key},
                                     files={"file": ("a.wav", fh, "audio/wav")},
                                     data={"model": STT_MODEL, "language_code": code}, timeout=120)
            tx = (resp.json() or {}).get("transcript", "") if resp.status_code == 200 else ""
            row["stt"] = tx if tx else ("HTTP " + str(resp.status_code) if resp.status_code != 200 else "(empty)")
            row["stt_ms"] = round((time.time() - t0) * 1000)
            row["roundtrip"] = "PASS" if tx else "FAIL"
        except Exception as e:
            row["stt"] = f"err {e}"; row["roundtrip"] = "FAIL"
        rows.append(row)

    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()