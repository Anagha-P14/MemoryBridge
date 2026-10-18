"""Bridge to your existing Alzheimer MRI model (ADNI + DWT features).

The model output is treated as ONE weak, optional *research screening signal*,
never as a diagnosis. We make its uncertainty visible:
  - normalised entropy of the class probabilities
  - top-1 vs top-2 margin
  - abstain (= 'uncertain') below thresholds
Fit `temperature` on a held-out set with eval/calibrate.py so probabilities
mean what they say (run it on the AMD GPU along with your retraining).
"""
import math

CLASSES = ["NonDemented", "VeryMild", "Mild", "Moderate"]  # adapt to your labels


def softmax(logits, T=1.0):
    m = max(logits)
    ex = [math.exp((x - m) / T) for x in logits]
    s = sum(ex)
    return [e / s for e in ex]


def summarize(probs: dict, min_conf=0.70, min_margin=0.20):
    items = sorted(probs.items(), key=lambda kv: -kv[1])
    (c1, p1), (c2, p2) = items[0], items[1]
    n = len(items)
    ent = -sum(p * math.log(p + 1e-12) for p in probs.values()) / math.log(n)
    uncertain = p1 < min_conf or (p1 - p2) < min_margin
    return {
        "top": c1, "top_prob": round(p1, 3), "runner_up": c2, "runner_up_prob": round(p2, 3),
        "entropy": round(ent, 3), "uncertain": uncertain,
        "message": ("Model is NOT confident (probabilities are close). Do not rely on this signal."
                    if uncertain else
                    "Screening signal only. Imaging model output is not a diagnosis; clinician must interpret."),
    }


def predict_from_model(image_path: str):
    """Hook: load your trained model here and return a dict of class->prob.
    e.g. torch.load('model.pt') -> logits -> softmax(logits, T=calibrated_T).
    Left as a stub so the app runs without the MRI data (ADNI licence!)."""
    raise NotImplementedError("Plug in your model; see README step 'Connect your MRI model'.")
