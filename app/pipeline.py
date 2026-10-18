"""Case pipeline: redact -> triage -> extract -> ground -> score -> route."""
import time
from . import llm, safety, grounding, mri_signal

QUESTION_TEMPLATES = {
    "memory": "Has the memory change progressed since the last visit? What should we track at home?",
    "daily_living": "Which daily tasks are still safe for them to do alone?",
    "behavior": "What can we do about the evening agitation / mood changes? Are any of these side effects?",
    "safety": "What safety steps are advised (door alarms, ID bracelet, stove safety)?",
    "sleep": "Could the sleep problems be related to the condition or to the medicines?",
    "mood": "Should low mood or withdrawal be assessed?",
    "medication": "Can we review how the medicines are given and how to avoid missed/double doses?",
    "prior_record": "Is a repeat cognitive test or scan due?",
}
CONF_REVIEW = 0.6      # below this, a finding needs a human look
MIN_CHARS = 200        # very short notes -> not enough to summarise reliably


def _label(conf, ground):
    if ground == "missing":
        return "unsupported"
    return "high" if conf >= 0.75 else "medium" if conf >= CONF_REVIEW else "low"


def run_case(caregiver_text, prior_letter="", target_lang="en", mri_probs=None, known_names=()):
    t0 = time.perf_counter()
    reasons = []

    # 1. Redact before anything leaves the app; mapping never leaves the server.
    cg_red, mapping = safety.redact(caregiver_text, known_names)
    pl_red, mapping2 = safety.redact(prior_letter or "", known_names)
    mapping.update(mapping2)
    full_red = cg_red + "\n" + pl_red

    # 2. Rule-based triage on RAW text (so redaction can't hide a red flag).
    flags = safety.triage(caregiver_text + "\n" + (prior_letter or ""))
    level = safety.overall_level(flags)

    # 3. Extract with LLM (or mock) and 4. ground every finding in its source.
    findings, modes, errors, unclear = [], set(), [], []
    for src, text in (("caregiver_note", cg_red), ("clinic_letter", pl_red)):
        if not text.strip():
            continue
        res, mode, err = llm.extract(text, src)
        modes.add(mode)
        if err:
            errors.append(err)
        unclear += res.get("unclear", [])
        for f in res.get("findings", []):
            status, score = grounding.verify_quote(f.get("quote", ""), text)
            conf = float(f.get("confidence", 0.5))
            findings.append({
                "source": src, "category": f.get("category", "other"),
                "summary": f.get("summary", ""), "quote": f.get("quote", ""),
                "confidence": round(conf, 2), "grounding": status,
                "caregiver_unsure": bool(f.get("caregiver_unsure")),
                "label": _label(conf, status),
            })

    unsupported = [f for f in findings if f["label"] == "unsupported"]
    shown = [f for f in findings if f["label"] != "unsupported"]   # unsupported never shown as fact
    low = [f for f in shown if f["label"] == "low" or f["caregiver_unsure"]]

    # 5. MRI signal (optional) with visible uncertainty.
    mri = mri_signal.summarize(mri_probs) if mri_probs else None

    # 6. Routing. The product NEVER marks a case "final": a clinician always signs off.
    if level == "urgent":
        status = "URGENT_ESCALATE"
        reasons.append("Urgent red flag detected by rule engine: contact the clinician/emergency services now.")
    elif level == "prompt":
        status = "PRIORITY_REVIEW"
        reasons.append("Red flag needs clinician attention first.")
    else:
        status = "READY_FOR_CLINICIAN_REVIEW"
    if unsupported:
        reasons.append(f"{len(unsupported)} model claim(s) removed: could not be found in the source text.")
    if low:
        reasons.append(f"{len(low)} finding(s) are low-confidence or the writer was unsure: verify with caregiver.")
    if mri and mri["uncertain"]:
        reasons.append("MRI model is uncertain: signal excluded from the summary headline.")
    if len(caregiver_text) < MIN_CHARS:
        reasons.append("Very short note: summary may be incomplete.")
    if errors:
        reasons.append("; ".join(errors) + " (no AI findings used; manual review required).")
    if not shown:
        reasons.append("No findings could be extracted: manual review required.")
    if status == "READY_FOR_CLINICIAN_REVIEW" and reasons:
        status = "NEEDS_CLINICIAN_REVIEW"

    # 7. Suggested discussion questions come from fixed templates, not free LLM text.
    cats = []
    for f in shown:
        if f["category"] in QUESTION_TEMPLATES and f["category"] not in cats:
            cats.append(f["category"])
    questions = [QUESTION_TEMPLATES[c] for c in cats]

    # 8. Caregiver-facing text in their language (static verified headings; body is machine-translated).
    h = llm.HEADINGS.get(target_lang, llm.HEADINGS["en"])
    body = "\n".join(f"- {f['summary']}" for f in shown if f["source"] == "caregiver_note")
    body_t, verified = llm.translate(body, target_lang)

    return {
        "status": status, "reasons": reasons, "red_flags": flags,
        "findings": shown, "removed_unsupported": unsupported, "unclear": unclear,
        "questions": questions, "mri": mri,
        "patient_view": {"lang": target_lang, "headings": h, "body": body_t,
                         "translation_verified": verified,
                         "disclaimer": h["note"]},
        "redaction": {"items_masked": len(mapping), "tags": sorted(mapping.keys()),
                      "sent_to_model": full_red.strip()},
        "meta": {"llm_mode": ",".join(sorted(modes)) or "none",
                 "latency_ms": round((time.perf_counter() - t0) * 1000, 1)},
        "_mapping": mapping,  # stripped before returning from API
    }
