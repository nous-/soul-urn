#!/usr/bin/env python3
"""Report where each kind of urn sound has its energy, to compare with the speaker.

    python3 tools/analyze_audio_spectrum.py                    # audio/source
    python3 tools/analyze_audio_spectrum.py audio/processed    # what goes on the board

For voice lines, bell jingles, effects and carry music it prints the frequency
range holding the middle 90% and 98% of the energy, and the share below 150 Hz
(under what the GRS 3FR-4 speaker can play) and above 11.025 kHz (cut by the
board's 22.05 kHz format). Needs ffmpeg and numpy.
"""
import glob
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATE = 48_000
FFT = 8192


def decode(path):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(RATE), "-"])
    return np.frombuffer(raw, dtype="<f4")


def power_spectrum(samples):
    if len(samples) < FFT:
        samples = np.pad(samples, (0, FFT - len(samples)))
    window = np.hanning(FFT)
    power = np.zeros(FFT // 2 + 1)
    frames = 0
    for start in range(0, len(samples) - FFT + 1, FFT // 2):
        power += np.abs(np.fft.rfft(samples[start:start + FFT] * window)) ** 2
        frames += 1
    return power / max(frames, 1)


def energy_range(freqs, power, low, high):
    cumulative = np.cumsum(power) / power.sum()
    return freqs[np.searchsorted(cumulative, low)], freqs[np.searchsorted(cumulative, high)]


def group(category):
    if category.startswith("carrying_jingles"):
        return "bell jingles"
    if category.startswith("carrying_"):
        return "carry music"
    if category.startswith("soul_urn"):
        return "effects"
    return "voice lines"


def main():
    folder = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "audio", "source"))
    freqs = np.fft.rfftfreq(FFT, 1 / RATE)
    audible = freqs >= 20
    freqs = freqs[audible]
    totals = {}
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.*"), recursive=True)):
        if not path.endswith((".mp3", ".wav")):
            continue
        power = power_spectrum(decode(path))[audible]
        name = group(os.path.relpath(path, folder).split(os.sep)[0])
        count, total = totals.get(name, (0, 0))
        totals[name] = (count + 1, total + power / power.sum())  # each clip counts equally
    print(f"{os.path.relpath(folder, ROOT)}")
    for name in ("voice lines", "bell jingles", "effects", "carry music"):
        if name not in totals:
            continue
        count, power = totals[name]
        a90, b90 = energy_range(freqs, power, 0.05, 0.95)
        a98, b98 = energy_range(freqs, power, 0.01, 0.99)
        below = power[freqs < 150].sum() / power.sum() * 100
        above = power[freqs > 11025].sum() / power.sum() * 100
        print(f"  {name:12} {count:3d} files   90%: {a90:5.0f}-{b90:6.0f} Hz   98%: {a98:5.0f}-{b98:6.0f} Hz"
              f"   <150 Hz: {below:4.1f}%   >11 kHz: {above:4.1f}%")


if __name__ == "__main__":
    main()
