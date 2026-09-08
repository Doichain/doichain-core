#!/usr/bin/env python3
"""
Summarise a rehearsal run from the miners' CSV logs (runs/<timestamp>/vmN-miner.csv,
one line per tick: unix_time,height,nBits,difficulty,found).

Prints, per VM, the block timeline around the attacker's departure: when blocks were
found, the interval between them, the difficulty (in units of the regtest minimum),
valve events (a block whose target is ~4x easier than the previous one), and the time
from the departure until block intervals are back near the target spacing.

  ./analyze-run.py runs/20260908-143000 [--leave 2026-09-08T14:20:51Z] [--target 60]
"""
import argparse
import csv
import glob
import os
from datetime import datetime, timezone

REGTEST_MIN_DIFF = 4.656542373906925e-10   # difficulty of nBits 207fffff


def target_of(bits_hex):
    b = int(bits_hex, 16)
    e, m = b >> 24, b & 0x7fffff
    return m << (8 * (e - 3)) if e > 3 else m >> (8 * (3 - e))


def load(path):
    rows = []
    with open(path) as f:
        for r in csv.reader(f):
            if len(r) < 5:
                continue
            rows.append((int(r[0]), int(r[1]), r[2], float(r[3]), int(r[4])))
    return rows


def blocks_from(rows):
    """Reconstruct the block timeline: first tick at which each new height was seen."""
    seen, out = -1, []
    for t, h, bits, diff, _found in rows:
        if h > seen:
            out.append((t, h, bits, diff))
            seen = h
    return out


def fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def find_leave_time(run_dir, explicit):
    if explicit:
        return int(datetime.strptime(explicit, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    candidates = glob.glob(os.path.join(run_dir, "at-leave.log"))
    candidates += glob.glob(os.path.join(os.path.dirname(run_dir.rstrip("/")), "..", "state", "at-leave.log"))
    for p in candidates:
        for line in open(p):
            if "recovery starts now" in line and "(" in line:
                iso = line.split("(")[-1].rstrip(")\n")
                try:
                    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    pass
    return None


def report(name, rows, leave_ts, target):
    blocks = blocks_from(rows)
    print("=" * 72)
    print(f"  {name}: {len(rows)} ticks, {blocks[-1][1]} blocks, {fmt(rows[0][0])}–{fmt(rows[-1][0])} UTC")
    print("=" * 72)
    peak = max(blocks, key=lambda b: b[3])
    print(f"  peak difficulty {peak[3] / REGTEST_MIN_DIFF:,.0f}x min at height {peak[1]} ({fmt(peak[0])})")
    if not leave_ts:
        print("  (no --leave time; showing last 12 blocks)")
        for (t, h, _bits, diff), (t0, *_rest) in zip(blocks[-12:], blocks[-13:]):
            print(f"  {fmt(t):>8} {h:>7} {t - t0:>8.0f}s {diff / REGTEST_MIN_DIFF:>12,.0f}")
        return

    pre = [b for b in blocks if b[0] < leave_ts]
    post = [b for b in blocks if b[0] >= leave_ts]
    base_height = pre[-1][1] if pre else 0
    print(f"  attacker left {fmt(leave_ts)} UTC at height ~{base_height}; {len(post)} blocks since")
    print(f"  {'time':>8} {'height':>7} {'interval':>9} {'diff/min':>12}  note")
    prev_t = pre[-1][0] if pre else None
    prev_tgt = target_of(pre[-1][2]) if pre else None
    recovered = None
    steady = 0
    for t, h, bits, diff in post:
        iv = (t - prev_t) if prev_t else 0
        tgt = target_of(bits)
        note = ""
        if prev_tgt and tgt >= prev_tgt * 3.5:
            note = "VALVE (x4 easier)"
        if iv and abs(iv - target) <= 0.25 * target:
            steady += 1
            if steady >= 5 and recovered is None:
                recovered = (t, h)
                note = (note + " " if note else "") + "<- 5 blocks near target"
        else:
            steady = 0
        print(f"  {fmt(t):>8} {h:>7} {iv:>8.0f}s {diff / REGTEST_MIN_DIFF:>12,.0f}  {note}")
        prev_t, prev_tgt = t, tgt
    if recovered:
        hours = (recovered[0] - leave_ts) / 3600
        print(f"  --> recovered {hours:.2f} h after departure, at height {recovered[1]} ({recovered[1] - base_height} blocks)")
    else:
        print("  --> not recovered within the log (or still running)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--leave", help="ISO time (UTC) the attacker left; default: from state/at-leave.log if present")
    ap.add_argument("--target", type=float, default=60.0, help="block target spacing in seconds")
    a = ap.parse_args()

    leave_ts = find_leave_time(a.run_dir, a.leave)
    for path in sorted(glob.glob(os.path.join(a.run_dir, "vm*-miner.csv"))):
        name = os.path.basename(path).split("-")[0]
        rows = load(path)
        if not rows:
            print(f"{name}: empty")
            continue
        report(name, rows, leave_ts, a.target)


if __name__ == "__main__":
    main()
