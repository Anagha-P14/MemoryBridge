"""LLM access. Two modes, same interface:

 LIVE : any OpenAI-compatible server. On AMD that is vLLM (ROCm) running on
        an MI300X Linux instance: LLM_BASE_URL=http://127.0.0.1:8000/v1 (use SSH tunnel)
 MOCK : deterministic keyword extractor so the whole demo + tests run offline.

The LLM only EXTRACTS and TRANSLATES. It never diagnoses, never decides
urgency, and everything it returns is checked by grounding.py.
"""
import json
import os
import re
import urllib.request

BASE = os.getenv("LLM_BASE_URL", "").rstrip("/")
MODEL = os.getenv("LLM_MODEL", "google/gemma-3-12b-it")
KEY = os.getenv("LLM_API_KEY", "EMPTY")

SYSTEM = """You help prepare a clinic visit for a person with suspected or known memory problems.
You are NOT a clinician. Rules:
1. Never diagnose, never suggest treatment, never change medication.
2. Extract only what the text explicitly says. Do not infer.
3. Every finding MUST include "quote": an exact, verbatim substring of the source text.
4. If the writer sounds unsure (maybe, I think), set "caregiver_unsure": true and lower confidence.
5. If something is unclear, put it in "unclear" instead of guessing.
Return ONLY JSON: {"language": "<iso code>", "findings": [{"category": "memory|daily_living|behavior|safety|sleep|mood|medication|prior_record",
"summary": "<neutral one sentence in English>", "quote": "<verbatim>", "confidence": 0-1, "caregiver_unsure": bool}], "unclear": ["..."]}"""

CATS = {
    "medication": r"\b(tablets?|pills?|medicines?|medication|doses?|donepezil|memantine|rivastigmine|mg)\b",
    "safety": r"\b(lost|wander|stove|gas|fell|fall|door|knife|missing)\b",
    "memory": r"\b(forget|forgot|forgets|forgetting|repeat|repeated|repeats|same question|remember|recall|names?)\b",
    "daily_living": r"\b(cook|cooking|bath|bathing|dress|dressing|bills?|money|shopping|phone|manage|managing|eat|eating)\b",
    "behavior": r"\b(angry|anger|aggress|shout|agitat|suspicious|accus|restless|evening|sundown|irritable)\b",
    "sleep": r"\b(sleep|sleeping|awake|nights?)\b",
    "mood": r"\b(sad|depress|withdrawn|anxious|crying|cry|apath|low mood|low in mood|mood)\b",
    "prior_record": r"\b(MMSE|MoCA|scan|MRI|diagnos|started|prescribed|referred)\b",
}
HEDGE = re.compile(r"\b(maybe|not sure|i think|perhaps|might|seems?|possibly|i guess)\b", re.I)


def _mock_extract(text: str, source: str):
    findings = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        sent = sent.strip()
        if len(sent) < 12:
            continue
        cats = [c for c, rx in CATS.items() if re.search(rx, sent, re.I)]
        if source == "clinic_letter":
            cats = [c for c in cats if c in ("prior_record", "medication")] or (["prior_record"] if cats else [])
        if not cats:
            continue
        unsure = bool(HEDGE.search(sent))
        conf = 0.8 - (0.25 if unsure else 0) - (0.1 if len(cats) > 2 else 0)
        findings.append({"category": cats[0], "summary": sent, "quote": sent,
                         "confidence": round(conf, 2), "caregiver_unsure": unsure})
    return {"language": "en", "findings": findings, "unclear": []}


def _live_extract(text: str):
    body = json.dumps({
        "model": MODEL, "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text}],
    }).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", body,
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        content = json.load(r)["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError("Model response did not contain a JSON object")
    result = json.loads(m.group(0))
    if not isinstance(result, dict) or not isinstance(result.get("findings", []), list):
        raise ValueError("Model response schema is invalid")
    for finding in result.get("findings", []):
        if not isinstance(finding, dict) or not isinstance(finding.get("quote"), str) or not isinstance(finding.get("summary"), str):
            raise ValueError("Model finding schema is invalid")
    if not isinstance(result.get("unclear", []), list):
        raise ValueError("Model uncertainty field is invalid")
    return result


def extract(text: str, source: str):
    """Returns (result_dict, mode, error). Never raises."""
    if BASE:
        try:
            return _live_extract(text), "live", None
        except Exception as e:  # fail closed: do not imply the model succeeded
            return {"language": "en", "findings": [], "unclear": ["AI extraction unavailable; clinician must review the original text manually."]}, "unavailable", f"LLM failed: {type(e).__name__}"
    return _mock_extract(text, source), "mock", None


HEADINGS = {  # static, human-verified strings (safe to show without a model)
    "en": {"title": "What you told us", "ask": "Questions to bring to the doctor", "note": "This is a summary of what you wrote. It is not a diagnosis. Your clinician will review it."},
    "hi": {"title": "आपने हमें क्या बताया", "ask": "डॉक्टर से पूछने के लिए प्रश्न", "note": "यह आपके लिखे हुए का सार है। यह निदान नहीं है। आपके डॉक्टर इसकी समीक्षा करेंगे।"},
    "kn": {"title": "ನೀವು ನಮಗೆ ಹೇಳಿದ್ದು", "ask": "ವೈದ್ಯರನ್ನು ಕೇಳಬೇಕಾದ ಪ್ರಶ್ನೆಗಳು", "note": "ಇದು ನೀವು ಬರೆದದ್ದರ ಸಾರಾಂಶ. ಇದು ರೋಗನಿರ್ಣಯವಲ್ಲ. ನಿಮ್ಮ ವೈದ್ಯರು ಪರಿಶೀಲಿಸುತ್ತಾರೆ."},
}


def translate(text: str, lang: str):
    """Returns (text, verified). Machine translation is never marked verified."""
    if lang == "en" or not BASE:
        return text, False
    try:
        body = json.dumps({"model": MODEL, "temperature": 0, "messages": [
            {"role": "system", "content": f"Translate to language code '{lang}'. Plain, simple wording. Output only the translation."},
            {"role": "user", "content": text}]}).encode()
        req = urllib.request.Request(f"{BASE}/chat/completions", body,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["choices"][0]["message"]["content"].strip(), False
    except Exception:
        return text, False
