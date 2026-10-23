"""Build app/data/paper_digits.npz from "Optical Recognition of Handwritten Digits" (Alpaydin & Kaynak, 1998, UCI,
CC BY 4.0, https://doi.org/10.24432/C50P49): the original 32 x 32 bitmaps, normalised as app/paper_digits.py does with
a scanned digit, counted in 4 x 4 blocks.

    python tools/build_paper_digits.py <folder>

<folder> holds the unpacked files optdigits-orig.tra, .wdep, .cv and .windep (the .Z files of the zip, decompressed,
e.g. with `gzip -dc optdigits-orig.tra.Z > optdigits-orig.tra`). Prints the share of the writer-independent test set
read right when the other three parts are the reference."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.paper_digits import features  # noqa: E402

PARTS = ("tra", "wdep", "cv", "windep")


def load(path):
    bitmaps, values, rows = [], [], []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if len(line) == 32 and set(line) <= {"0", "1"}:
                rows.append([ch == "1" for ch in line])
            elif len(rows) == 32 and line.strip().isdigit():
                bitmaps.append(np.array(rows, bool))
                values.append(int(line.strip()))
                rows = []
            else:
                rows = []
    return bitmaps, values


def main(folder):
    data = {p: load(os.path.join(folder, f"optdigits-orig.{p}")) for p in PARTS}
    x = {p: np.array([features(b) for b in data[p][0]], np.uint8) for p in PARTS}
    y = {p: np.array(data[p][1], np.uint8) for p in PARTS}
    ref = np.concatenate([x[p] for p in PARTS[:3]]).astype(np.float32)
    ref_y = np.concatenate([y[p] for p in PARTS[:3]])
    test = x["windep"].astype(np.float32)
    d = ((test[:, None, :] - ref[None]) ** 2).sum(-1)
    print(f"read right: {(ref_y[d.argmin(1)] == y['windep']).mean():.3f}")
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "paper_digits.npz")
    np.savez_compressed(out, x=np.concatenate([x[p] for p in PARTS]), y=np.concatenate([y[p] for p in PARTS]))
    print(out)


if __name__ == "__main__":
    main(sys.argv[1])
