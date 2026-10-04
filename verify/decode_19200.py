#!/usr/bin/env python3
"""Decode verify/captures/display-19200-*.bin (CC/CE oversampled line)."""

from collections import Counter
from pathlib import Path

p = sorted(Path(__file__).resolve().parent.joinpath("captures").glob("display-19200-*.bin"))[-1]
raw = p.read_bytes()
print("file", p.name, "len", len(raw), "uniq", Counter(raw).most_common(8))

bits = [1 if (b & 2) else 0 for b in raw]  # CE vs CC
runs = []
cur, n = bits[0], 1
for x in bits[1:]:
    if x == cur:
        n += 1
    else:
        runs.append((cur, n))
        cur, n = x, 1
runs.append((cur, n))
print("runs", Counter(n for _, n in runs).most_common(8), "(2–3 = ~9600 NRZ sampled at 19200)")
