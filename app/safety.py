"""Deterministic safety layer. Runs BEFORE and independently of any LLM.

Why rules and not the model: red-flag detection must be predictable, auditable
and must never be "talked out of" by a model. The LLM can add context; it can
never remove a red flag.
"""
import re
from dataclasses import dataclass

# ---------------------------------------------------------------- red flags
# level: "urgent" = tell the user to contact emergency/clinician NOW
#        "prompt" = clinician must look at this case first (same day)
@dataclass
class Rule:
    id: str
    level: str
    label: str
    pattern: str

RULES = [
    Rule("self_harm", "urgent", "Possible self-harm / suicidal statement",
         r"(want(s|ed)? to die|kill (him|her|them)self|end (his|her|their) life|suicid|मरना चाहत|आत्महत्या|जान दे)"),
    Rule("stroke_signs", "urgent", "Possible stroke signs (sudden weakness, face droop, slurred speech)",
         r"(sudden(ly)?[^.]{0,40}(weak|confus|slurred|can'?t speak|numb)|face (is )?droop|slurred speech|एकदम से|अचानक[^.]{0,30}(कमजोर|बोल))"),
    Rule("head_injury", "urgent", "Fall with head injury or loss of consciousness",
         r"(fell|fall|fallen)[^.]{0,60}(hit (his|her|the) head|head injury|blacked out|unconscious|bleeding)"),
    Rule("not_eating_drinking", "urgent", "Not eating or drinking",
         r"(not (been )?(eating|drinking)|(stopped|barely) (eating|drinking)|refus(es|ed|ing) (food|water|to eat|to drink)|खाना नहीं खा|पानी नहीं पी)"),
    Rule("med_double_dose", "prompt", "Possible medication error (double / extra dose)",
         r"((took|taken|given|gave|had)[^.]{0,50}(twice|double|two times|extra|again)|extra (tablet|pill|dose)|दो बार (दवा|गोली)|दवा दो बार)"),
    Rule("wandering_lost", "prompt", "Got lost / wandering outside the home",
         r"(got lost|was lost|lost (his|her) way|couldn'?t find (his|the|her) way|wander(ed|ing)|went missing|खो गए|भटक|ಕಳೆದುಹೋ)"),
    Rule("aggression", "prompt", "Aggression or risk to others",
         r"(hit (me|us|her|him)|threaten|violent|aggressive|pushed me|हाथ उठा|मारने)"),
    Rule("caregiver_crisis", "prompt", "Caregiver at breaking point",
         r"(can'?t cope|cannot cope|at (my|our) wit'?s end|burn(ed|t)? out|exhausted and alone|थक गई हूँ|थक गया हूँ)"),
]
_COMPILED = [(r, re.compile(r.pattern, re.I)) for r in RULES]


def triage(text: str):
    """Return list of {id, level, label, evidence} for every rule that fires."""
    hits = []
    for rule, rx in _COMPILED:
        m = rx.search(text)
        if m:
            s = max(0, m.start() - 50)
            hits.append({"id": rule.id, "level": rule.level, "label": rule.label,
                         "evidence": text[s:m.end() + 50].replace("\n", " ").strip()})
    return hits


def overall_level(hits):
    if any(h["level"] == "urgent" for h in hits):
        return "urgent"
    return "prompt" if hits else "routine"


# --------------------------------------------------------------- redaction
# Minimal, transparent PHI scrubbing before text leaves the app (e.g. to a
# hosted GPU). It is a *risk reducer*, not anonymisation: regexes miss things,
# so the app also supports a self-hosted model on your own AMD instance and
# shows the user exactly what was sent.
_PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,11}(?!\d)")),
    ("DATE", re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")),
    ("ID", re.compile(r"\b(?:MRN|UHID|ID|Aadhaar)[:#\s]*[A-Z0-9-]{5,}\b", re.I)),
    ("NAME", re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Shri|Smt)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")),
]


def redact(text: str, known_names=()):
    """Returns (redacted_text, mapping). Mapping stays server-side only."""
    mapping, counters = {}, {}

    def sub(kind):
        def _f(m):
            counters[kind] = counters.get(kind, 0) + 1
            tag = f"[{kind}_{counters[kind]}]"
            mapping[tag] = m.group(0)
            return tag
        return _f

    out = text
    for name in known_names:
        if name:
            out = re.sub(re.escape(name), sub("NAME"), out, flags=re.I)
    for kind, rx in _PATTERNS:
        out = rx.sub(sub(kind), out)
    return out, mapping
