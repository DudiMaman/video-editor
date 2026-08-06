#!/usr/bin/env python3
"""Classify a video's narration voice: female / male / none.

Extracts mono 16k audio, finds voiced frames via autocorrelation pitch
tracking on short windows, and reports the median F0 of confidently
voiced, speech-range frames. Median F0 >= 165 Hz -> female, 80-165 ->
male, too few voiced frames -> none (music/ambient only).
"""
import subprocess, sys, tempfile, wave, math
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
    return sr / peak, float(seg.max())

def main(path):
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-t", "30",
                        "-vn", "-ac", "1", "-ar", "16000", tmp.name], check=True)
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
    print("female" if med >= 165 else "male",
          f"# median_f0={med:.0f}Hz voiced={voiced_ratio:.0%}", file=sys.stderr)
    print("female" if med >= 165 else "male")

if __name__ == "__main__":
    main(sys.argv[1])
