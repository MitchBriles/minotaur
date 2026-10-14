#!/usr/bin/env python3
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
"""
Proves census records with alive-tv.

The census recovers each matrix from basis vectors and screens it on random
inputs, which is strong evidence but not a proof. Every record carries its
region as a standalone function @src; this builds the matching @tgt, one ABM
over the concatenation of the region's inputs, and asks alive-tv whether the
rewrite is a refinement.

Identical regions recur across files and projects, so results are memoized in
a JSONL file keyed by a hash of (region, op, matrix, mask). A rerun only proves
what it has not seen, and an interrupted run loses nothing already written.

  abm-verify.py -j 32 --memo verified.jsonl census.jsonl
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ALIVE_TV = os.environ.get(
    "ALIVE_TV", os.path.expanduser("~/research/alive2/build/alive-tv"))


def record_key(r):
    h = hashlib.sha1()
    for part in (r["cut"], r.get("op", ""), r["matrix"], r["mask"]):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


def diagonals(r):
    """Groups the matrix by shift distance d = out bit - in bit. Each group is
    one (x << d) & mask, so OR-ing or XOR-ing the groups computes exactly the
    ABM: every (output, input) pair lands in exactly one group."""
    out, inn = r["width"], r["in_width"]
    m = int(r["matrix"], 16)
    groups = {}
    i = 0
    while m:
        if m & 1:
            row, j = divmod(i, inn)
            o = out - 1 - row
            groups[o - j] = groups.get(o - j, 0) | (1 << o)
        m >>= 1
        i += 1
    return groups


def target_ir(r, use_call=False):
    """@tgt: concatenate the inputs, first input in the low bits, then either
    call ABM or (the default) build the same function from shifts and masks,
    which the solver proves far faster for wide XOR matrices."""
    ins = [i["w"] for i in r["inputs"]]
    total, out = r["in_width"], r["width"]
    fn = "ABMX" if r.get("op") == "xor" else "ABM"
    args = ", ".join(f"i{w} %a{k}" for k, w in enumerate(ins))
    body, acc, off = [], None, 0
    for k, w in enumerate(ins):
        v = f"%a{k}"
        if w != total:
            body.append(f"  %z{k} = zext i{w} {v} to i{total}")
            v = f"%z{k}"
        if off:
            body.append(f"  %s{k} = shl i{total} {v}, {off}")
            v = f"%s{k}"
        if acc is None:
            acc = v
        else:
            body.append(f"  %c{k} = or i{total} {acc}, {v}")
            acc = f"%c{k}"
        off += w
    m, b = int(r["matrix"], 16), int(r["mask"], 16)
    if use_call:
        body.append(f"  %r = call i{out} @{fn}(i{total} {acc}, "
                    f"i{out * total} {m}, i{out} {b})")
        body.append(f"  ret i{out} %r")
        return (f"define i{out} @tgt({args}) {{\n" + "\n".join(body) +
                f"\n}}\ndeclare i{out} @{fn}(i{total}, i{out * total}, "
                f"i{out})\n")

    # the input is used once per diagonal; freeze it so an undef input is one
    # value throughout, as it is in the single-use ABM call
    body.append(f"  %xf = freeze i{total} {acc}")
    acc = "%xf"
    w = max(total, out)
    if w != total:
        body.append(f"  %xw = zext i{total} {acc} to i{w}")
        acc = "%xw"
    comb = "xor" if fn == "ABMX" else "or"
    res = None
    for n, (d, mask) in enumerate(sorted(diagonals(r).items())):
        v = acc
        if d:
            op = "shl" if d > 0 else "lshr"
            body.append(f"  %d{n} = {op} i{w} {acc}, {abs(d)}")
            v = f"%d{n}"
        body.append(f"  %t{n} = and i{w} {v}, {mask}")
        if res is None:
            res = f"%t{n}"
        else:
            body.append(f"  %u{n} = {comb} i{w} {res}, %t{n}")
            res = f"%u{n}"
    if res is None:
        res = "0"
    if w != out:
        body.append(f"  %tr = trunc i{w} {res} to i{out}")
        res = "%tr"
    body.append(f"  %r = xor i{out} {res}, {b}")
    body.append(f"  ret i{out} %r")
    return f"define i{out} @tgt({args}) {{\n" + "\n".join(body) + "\n}\n"


def verify(r, smt_to, mem_mb, wall, use_call=False):
    with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False) as f:
        f.write(r["cut"] + "\n" + target_ir(r, use_call))
        path = f.name
    cmd = ["prlimit", f"--as={mem_mb << 20}", "timeout", "-k", "10",
           str(wall), ALIVE_TV, f"-smt-to={smt_to * 1000}",
           # the question is what the region computes on defined values
           "-disable-undef-input", "-disable-poison-input", path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = p.stdout + p.stderr
    finally:
        os.unlink(path)
    if "Transformation seems to be correct" in out:
        return "verified"
    if "Transformation doesn't verify" in out:
        return "refuted"
    if "Timeout" in out or p.returncode in (124, 137):
        return "timeout"
    if "Out of memory" in out or "memory" in out.lower() and p.returncode:
        return "oom"
    tail = out.strip().splitlines()[-1:] or [f"rc={p.returncode}"]
    return "error: " + tail[0][:200]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--memo", required=True,
                    help="JSONL of {key, verdict}; read, then appended to")
    ap.add_argument("-j", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--smt-to", type=int, default=30, help="seconds per query")
    ap.add_argument("--mem", type=int, default=4096, help="MB per alive-tv")
    ap.add_argument("--abm-call", action="store_true",
                    help="target calls @ABM/@ABMX instead of the equivalent "
                         "shift/mask form (slower for wide XOR)")
    ap.add_argument("--limit", type=int, default=0,
                    help="prove at most this many new regions (0: all)")
    a = ap.parse_args()

    memo = {}
    if os.path.exists(a.memo):
        with open(a.memo) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    memo[e["key"]] = e["verdict"]
                except (ValueError, KeyError):
                    pass

    todo, seen = [], set(memo)
    for path in a.logs:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "census" or "cut" not in r:
                    continue
                k = record_key(r)
                if k not in seen:
                    seen.add(k)
                    todo.append((k, r))
    if a.limit:
        todo = todo[:a.limit]
    print(f"[abm-verify] {len(memo)} memoized, {len(todo)} to prove",
          file=sys.stderr)

    counts = {}
    wall = a.smt_to * 4 + 30
    with open(a.memo, "a") as out, cf.ThreadPoolExecutor(a.j) as ex:
        futs = {ex.submit(verify, r, a.smt_to, a.mem, wall, a.abm_call): k
                for k, r in todo}
        for n, fut in enumerate(cf.as_completed(futs), 1):
            v = fut.result()
            out.write(json.dumps({"key": futs[fut], "verdict": v}) + "\n")
            out.flush()
            v0 = v.split(":")[0]
            counts[v0] = counts.get(v0, 0) + 1
            if n % 100 == 0:
                print(f"[abm-verify] {n}/{len(todo)} {counts}",
                      file=sys.stderr)
    print(f"[abm-verify] done: {counts}", file=sys.stderr)


if __name__ == "__main__":
    main()
