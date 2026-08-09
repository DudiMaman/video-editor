#!/usr/bin/env python3
"""Classify a video's narration voice: female / male / none.

Extracts mono 16k audio through a steep 140 Hz high-pass, finds voiced
frames via autocorrelation pitch tracking on short windows, and reports
the median F0 of confidently voiced, speech-range frames. Median F0 >=
162 Hz -> female, below -> male, too few voiced frames -> none
(music/ambient only).

The high-pass is load-bearing: TikTok background music carries a strong
bass line at 80-100 Hz that otherwise dominates the autocorrelation and
drags a 170 Hz narrator down to a "male" reading (this shipped a male
outro on a female-narrated video once). Speech survives the filter -
even a male voice whose fundamental is removed keeps its periodicity
through the harmonic spacing - while near-sinusoidal bass disappears.

Calibrated on hand-verified narrators (post-filter medians): females
167/186/192/193/213 Hz, males 130/148/157 Hz. 162 sits midway between
the closest pair (157 male sago, 167 female syngonium).
"""
import subprocess, sys, tempfile, wave
import numpy as np

def f0_autocorr(frame, sr):
    frame = frame - frame.mean()
    if np.abs(frame).max() < 200:  # silence (int16 scale)
        return None, 0.0
    frame = frame / (np.abs(frame).max() + 1e-9)
    corr = np.correlate(frame, frame, mode="full")[len(frame)-1:]
    corr /= (corr[0] + 1e-9)
    lo, hi = int(sr/300), int(sr/70)  # 70-300 Hz
    if hi >= len(corr):
        return None, 0.0
    seg = corr[lo:hi]
    peak = int(np.argmax(seg)) + lo
    conf = float(seg.max())
    # A voice at F0 also peaks at 2x/3x its period (octave error), and
    # residual music can tip the balance toward the subharmonic. Prefer
    # the highest frequency whose peak is nearly as strong as the max.
    best = peak
    for div in (2, 3):
        cand = peak // div
        if cand < lo:
            continue
        w = max(2, cand // 20)
        lo2, hi2 = max(lo, cand - w), min(hi, cand + w + 1)
        if hi2 <= lo2:
            continue
        local = int(np.argmax(corr[lo2:hi2])) + lo2
        if corr[local] >= 0.85 * conf:
            best = local
    return sr / best, float(corr[best])

def main(path):
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-t", "30",
                        "-vn", "-ac", "1", "-ar", "16000",
                        "-af", "highpass=f=140,highpass=f=140", tmp.name],
                       check=True)
        with wave.open(tmp.name) as w:
            sr = w.getframerate()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    win = int(sr * 0.05)
    f0s = []
    for i in range(0, len(data) - win, win // 2):
        f0, conf = f0_autocorr(data[i:i+win], sr)
        if f0 and conf > 0.55:
            f0s.append(f0)
    total_frames = max(1, (len(data) - win) // (win // 2))
    voiced_ratio = len(f0s) / total_frames
    if voiced_ratio < 0.12 or len(f0s) < 20:
        print("none"); return
    med = float(np.median(f0s))
    print("female" if med >= 162 else "male",
          f"# median_f0={med:.0f}Hz voiced={voiced_ratio:.0%}", file=sys.stderr)
    print("female" if med >= 162 else "male")

if __name__ == "__main__":
    main(sys.argv[1])
