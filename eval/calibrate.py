"""Temperature-scale your MRI classifier so its probabilities are trustworthy.
Usage (on AMD GPU box, after training):  python -m eval.calibrate val_logits.npy val_labels.npy
Prints best T and ECE before/after. Put T into mri_signal.softmax(logits, T)."""
import sys, numpy as np

def softmax(z, T): z = z / T; z -= z.max(1, keepdims=True); e = np.exp(z); return e / e.sum(1, keepdims=True)
def nll(z, y, T): p = softmax(z, T); return -np.log(p[np.arange(len(y)), y] + 1e-12).mean()
def ece(p, y, bins=10):
    conf, pred = p.max(1), p.argmax(1); acc = (pred == y); e = 0
    for lo in np.linspace(0, 1, bins, endpoint=False):
        m = (conf > lo) & (conf <= lo + 1 / bins)
        if m.any(): e += m.mean() * abs(acc[m].mean() - conf[m].mean())
    return e

if __name__ == "__main__":
    z, y = np.load(sys.argv[1]), np.load(sys.argv[2]).astype(int)
    Ts = np.linspace(0.5, 5, 91); T = Ts[int(np.argmin([nll(z, y, t) for t in Ts]))]
    print(f"T={T:.2f}  ECE before={ece(softmax(z,1),y):.3f} after={ece(softmax(z,T),y):.3f}")
