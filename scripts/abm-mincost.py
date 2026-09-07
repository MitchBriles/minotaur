#!/usr/bin/env python3

# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.

"""How many ordinary instructions would an ABM matrix cost without ABM?

An ABM is one cheap unary instruction by premise, so its cost carries no
information: every candidate is cost 1 and a cost filter can only say "yes".
What decides whether a matrix deserves opcode space is instead

    frequency x savings,   savings = (cheapest ABM-free implementation) - 1

This computes the second factor. For each matrix it searches for the shortest
straight-line sequence of ordinary instructions that reproduces the matrix, up
to --max-len, and reports that length. Matrices that need only one or two
instructions are already covered by existing ISAs and are not candidates.

The search is bounded and test driven: candidates are compared against the
matrix on a fixed sample of inputs, so a reported sequence is strong evidence
rather than a proof. Pass --verify to prove the winner with alive-tv.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import os

ALIVE_TV = os.environ.get(
    "ALIVE_TV", os.path.expanduser("~/research/alive2/build/alive-tv"))


def abm_apply(x, matrix, mask, n, exclusive):
    """out[n-1-i] = OP over j of M[i*n+j] & x[j], then ^ mask.

    Mirrors ABM_impl in alive2's smt/expr.cpp and abmApply in
    lib/enumerator.cpp.
    """
    res = 0
    for i in range(n):
        acc = 0
        for j in range(n):
            if (matrix >> (i * n + j)) & 1 and (x >> j) & 1:
                if not exclusive:
                    acc = 1
                    break
                acc ^= 1
        if acc:
            res |= 1 << (n - 1 - i)
    return res ^ mask


def samples(n, count=48, known_zero=0, known_one=0):
    """Inputs to compare on: edge cases first, then a fixed pseudo-random tail.

    known_zero/known_one restrict the sample to inputs that can actually reach
    the site. Without that a matrix is judged on inputs the program never
    produces, which is what makes a smear that is really x-1 look exotic.
    """
    full = (1 << n) - 1

    def fit(v):
        return ((v & ~known_zero) | known_one) & full
    fixed = [0, 1, 2, 3, full, full ^ 1, 1 << (n - 1), (1 << (n - 1)) - 1]
    if n >= 8:
        fixed += [0xFF & full, 0x0F & full, full ^ 0xFF, 1 << (n // 2)]
    out = list(dict.fromkeys(fit(v) for v in fixed))
    state = 0x9E3779B97F4A7C15
    while len(out) < count:
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        out.append(fit(state))
    return out[:count]


# ---------------------------------------------------------------------------
# Search
#
# Depth 0 to 2 is exhaustive, so "needs 3 or more" is an exact answer and that
# is the bar we actually care about. Depth 3 is then reached by inverting the
# final instruction and looking its operand up in the depth-2 table, which is
# exact for the invertible finals (xor, add, sub, not) and misses sequences
# ending in a lossy one such as and or shl. So a reported 3 is real, while a
# reported "3 or more" is a lower bound rather than a proof of exactly 3.
# ---------------------------------------------------------------------------

import numpy as np


def _transforms(n):
    """(label, fn) pairs taking a uint64 vector to a uint64 vector."""
    full = np.uint64((1 << n) - 1)
    one = np.uint64(1)
    out = []

    def add_const(name, f):
        for c in _const_pool(n):
            cc = np.uint64(c)
            out.append((f"{name} %v, {c}", lambda v, cc=cc, f=f: f(v, cc) & full))

    add_const("and", lambda v, c: v & c)
    add_const("or", lambda v, c: v | c)
    add_const("xor", lambda v, c: v ^ c)
    add_const("add", lambda v, c: v + c)
    add_const("sub", lambda v, c: v - c)
    add_const("mul", lambda v, c: v * c)

    # single instructions in LLVM IR and on real ISAs. Without these the
    # search charges three instructions for a byte swap and would report the
    # operations existing ISAs already cover as candidates.
    for k in range(1, n):
        kk, rk = np.uint64(k), np.uint64(n - k)
        out.append((f"call @llvm.fshl(%v, %v, {k})",
                    lambda v, kk=kk, rk=rk: ((v << kk) | (v >> rk)) & full))
    if n >= 16 and n % 8 == 0:
        nb = n // 8
        def _bswap(v, nb=nb):
            r = np.zeros_like(v)
            for b in range(nb):
                byte = (v >> np.uint64(8 * b)) & np.uint64(0xFF)
                r = r | (byte << np.uint64(8 * (nb - 1 - b)))
            return r & full
        out.append(("call @llvm.bswap(%v)", _bswap))

    def _bitrev(v):
        r = np.zeros_like(v)
        for i in range(n):
            r = r | (((v >> np.uint64(i)) & np.uint64(1)) << np.uint64(n - 1 - i))
        return r & full
    out.append(("call @llvm.bitreverse(%v)", _bitrev))

    for k in range(1, n):
        kk = np.uint64(k)
        out.append((f"shl %v, {k}", lambda v, kk=kk: (v << kk) & full))
        out.append((f"lshr %v, {k}", lambda v, kk=kk: v >> kk))

        def _ashr(v, kk=kk, k=k):
            sign = (v >> np.uint64(n - 1)) & one
            hi = np.uint64(((1 << n) - 1) ^ (((1 << n) - 1) >> k))
            return ((v >> kk) | (sign * hi)) & full
        out.append((f"ashr %v, {k}", _ashr))
    return out


def _binary_with_x(n):
    full = np.uint64((1 << n) - 1)
    return [
        ("and %v, %x", lambda v, x: v & x),
        ("or %v, %x", lambda v, x: v | x),
        ("xor %v, %x", lambda v, x: v ^ x),
        ("add %v, %x", lambda v, x: (v + x) & full),
        ("sub %v, %x", lambda v, x: (v - x) & full),
    ]


_POOL_CACHE = {}


def _const_pool(n):
    if n in _POOL_CACHE:
        return _POOL_CACHE[n]
    full = (1 << n) - 1
    pool = {0, 1, full}
    for k in range(n):
        pool.add(1 << k)
        pool.add((1 << (k + 1)) - 1)
        pool.add(full ^ (1 << k))
    _POOL_CACHE[n] = sorted(pool)
    return _POOL_CACHE[n]


class Expr:
    """A candidate sequence: the IR text, its instruction count, behaviour."""
    __slots__ = ("text", "length", "vals")

    def __init__(self, text, length, vals):
        self.text = text
        self.length = length
        self.vals = vals


def search(target_vals, xs, n, mask, max_len):
    """Shortest sequence reproducing the matrix, or None."""
    x = np.array(xs, dtype=np.uint64)
    target = np.array(target_vals, dtype=np.uint64)

    if np.array_equal(x, target):
        return Expr("%x", 0, tuple(xs))

    steps = _transforms(n) + [(lbl, (lambda f: lambda v: f(v, x))(fn))
                              for lbl, fn in _binary_with_x(n)]

    # level 1
    rows, texts = [], []
    for lbl, fn in steps:
        rows.append(fn(x))
        texts.append(lbl)
    L1 = np.array(rows, dtype=np.uint64)
    hit = np.flatnonzero((L1 == target).all(axis=1))
    if hit.size:
        return Expr("%x\n  " + texts[hit[0]], 1, target_vals)

    # dedupe on behaviour before expanding
    _, keep = np.unique(L1, axis=0, return_index=True)
    keep = np.sort(keep)
    L1, texts = L1[keep], [texts[i] for i in keep]

    # level 2, vectorised across all level-1 rows at once
    table = {}
    for i in range(L1.shape[0]):
        table.setdefault(L1[i].tobytes(), (1, texts[i]))
    table.setdefault(x.tobytes(), (0, ""))

    for lbl, fn in steps:
        out = fn(L1)
        hit = np.flatnonzero((out == target).all(axis=1))
        if hit.size:
            i = int(hit[0])
            return Expr("%x\n  " + texts[i] + "\n  " + lbl, 2, target_vals)
        for i in range(out.shape[0]):
            table.setdefault(out[i].tobytes(), (2, texts[i] + "\n  " + lbl))

    if max_len < 3:
        return None

    # depth 3: invert the final instruction and look the operand up
    full = (1 << n) - 1
    inverses = []
    for c in _const_pool(n):
        inverses.append((f"xor %v, {c}", lambda t, c=c: t ^ np.uint64(c)))
        inverses.append((f"add %v, {c}",
                         lambda t, c=c: (t - np.uint64(c)) & np.uint64(full)))
        inverses.append((f"sub %v, {c}",
                         lambda t, c=c: (t + np.uint64(c)) & np.uint64(full)))
    for lbl, inv in inverses:
        pred = inv(target)
        got = table.get(pred.tobytes())
        if got is not None:
            length, text = got
            if length + 1 <= max_len:
                body = ("%x" if not text else "%x\n  " + text) + "\n  " + lbl
                return Expr(body, length + 1, target_vals)
    return None


def lower_matrix(matrix, mask, n, op):
    """A working implementation for any matrix, as an upper bound on cost.

    Two shapes cover most of what real matrices do. An input bit feeding many
    outputs is a broadcast, `-(x>>j & 1) & outmask`, which is how a conditional
    constant such as a CRC polynomial appears. Everything left is grouped by
    offset, where each group is one mask and one shift. Groups combine with the
    semiring's operator. Unlike the bounded search this always succeeds, so
    every matrix gets a concrete implementation.
    """
    pairs = set()
    for i in range(n):
        o = n - 1 - i
        for j in range(n):
            if (matrix >> (i * n + j)) & 1:
                pairs.add((o, j))
    if not pairs:
        return [f"%r = i{n} {mask}"], 0

    full = (1 << n) - 1
    join = "xor" if op == "xor" else "or"
    steps, terms, counter = [], [], 0

    def emit(text):
        nonlocal counter
        name = f"%t{counter}"
        counter += 1
        steps.append(f"{name} = {text}")
        return name

    fan = {}
    for o, j in pairs:
        fan.setdefault(j, []).append(o)

    for j in sorted(fan, key=lambda j: (-len(fan[j]), j)):
        outs = [o for o in fan[j] if (o, j) in pairs]
        if len(outs) < 3:
            continue
        outmask = 0
        for o in outs:
            outmask |= 1 << o
        v = "%x"
        if j:
            v = emit(f"lshr i{n} {v}, {j}")
        v = emit(f"and i{n} {v}, 1")
        v = emit(f"sub i{n} 0, {v}")
        v = emit(f"and i{n} {v}, {outmask}")
        terms.append(v)
        pairs -= {(o, j) for o in outs}

    deltas = {}
    for o, j in pairs:
        deltas.setdefault(o - j, 0)
        deltas[o - j] |= 1 << j
    for d in sorted(deltas, key=lambda d: (-abs(d), d)):
        inmask = deltas[d]
        v = "%x"
        # the shift discards the bits the mask would have cleared, so the and
        # is only needed when the mask is narrower than that
        if d > 0:
            redundant = (full >> d)
        elif d < 0:
            redundant = (full << -d) & full
        else:
            redundant = full
        if inmask != full and (inmask | ~redundant) & full != full:
            v = emit(f"and i{n} {v}, {inmask}")
        if d > 0:
            v = emit(f"shl i{n} {v}, {d}")
        elif d < 0:
            v = emit(f"lshr i{n} {v}, {-d}")
        terms.append(v)

    acc = terms[0]
    for t in terms[1:]:
        acc = emit(f"{join} i{n} {acc}, {t}")
    if mask:
        acc = emit(f"xor i{n} {acc}, {mask}")
    return steps, len(steps)


def emit_ir(expr, n, matrix, mask, op):
    """LLVM IR pairing the found sequence with the ABM call, for alive-tv."""
    lines, cur = [], "%x"
    for k, step in enumerate(expr.text.split("\n")[1:]):
        step = step.strip()
        if not step:
            continue
        name = f"%t{k}"
        # steps are stored as "<op> %v, <rhs>"; LLVM needs the type in between
        opcode, rest = step.split(" ", 1)
        lines.append(f"  {name} = {opcode} i{n} {rest.replace('%v', cur)}")
        cur = name
    fn = "ABMX" if op == "xor" else "ABM"
    body = "\n".join(f"  {l.strip()}" for l in lines)
    return f"""define i{n} @src(i{n} %x) {{
{body}
  ret i{n} {cur}
}}

define i{n} @tgt(i{n} %x) {{
  %r = call i{n} @{fn}(i{n} %x, i{n * n} {matrix}, i{n} {mask})
  ret i{n} %r
}}

declare i{n} @{fn}(i{n}, i{n * n}, i{n})
"""


def verify(ir, timeout=120):
    with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False) as f:
        f.write(ir)
        path = f.name
    try:
        r = subprocess.run(["timeout", str(timeout), ALIVE_TV,
                            "-smt-to=30000", path],
                           capture_output=True, text=True)
        out = r.stdout + r.stderr
        if "seems to be correct" in out and "ERROR" not in out:
            return "verified"
        if "Timeout" in out:
            return "smt timeout"
        return "NOT verified"
    except OSError as e:
        return f"alive-tv unavailable ({e.strerror})"
    finally:
        os.unlink(path)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="*", default=["-"],
                    help="JSONL logs from -minotaur-abm-log")
    ap.add_argument("--max-len", type=int, default=3, metavar="N",
                    help="longest sequence to search for (default 3, the bar "
                         "for being worth an instruction)")
    ap.add_argument("--width-max", type=int, default=32, metavar="N",
                    help="skip matrices wider than this; the search is O(n) "
                         "per candidate (default 32)")
    ap.add_argument("--verify", action="store_true",
                    help="prove the winning sequence with alive-tv")
    ap.add_argument("--ignore-preconditions", action="store_true",
                    help="judge matrices on all inputs, not just those the "
                         "known bits say can reach the site")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    records = []
    for path in args.logs:
        stream = sys.stdin if path == "-" else open(path)
        for line in stream:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    pass
        if stream is not sys.stdin:
            stream.close()

    groups = {}
    for r in records:
        try:
            key = (r.get("op", "or"), int(r["width"]), int(r["matrix"], 16),
                   int(r["mask"], 16) if r.get("mask") else 0,
                   int(r.get("known_zero") or "0", 16),
                   int(r.get("known_one") or "0", 16))
        except (KeyError, ValueError, TypeError):
            continue
        groups.setdefault(key, 0)
        groups[key] += 1

    out = []
    for (op, n, matrix, mask, kz, ko), count in sorted(groups.items(),
                                                       key=lambda kv: -kv[1]):
        if n > args.width_max:
            continue
        if args.ignore_preconditions:
            kz = ko = 0
        xs = samples(n, known_zero=kz, known_one=ko)
        target = tuple(abm_apply(v, matrix, mask, n, op == "xor") for v in xs)
        found = search(target, xs, n, mask, args.max_len)

        entry = {"op": op, "width": n, "count": count,
                 "matrix": f"0x{matrix:x}", "mask": f"0x{mask:x}",
                 "known_zero": f"0x{kz:x}", "known_one": f"0x{ko:x}",
                 "min_instructions": found.length if found else None,
                 "savings": (found.length - 1) if found else None}
        if found:
            entry["sequence"] = [s.strip() for s in found.text.split("\n")[1:]]
            if args.verify and found.length:
                entry["proof"] = verify(emit_ir(found, n, matrix, mask, op))
        out.append(entry)

    if args.json:
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0

    for e in out:
        n = e["min_instructions"]
        verdict = (f"{n} instruction(s)" if n is not None
                   else f"more than {args.max_len}, a real candidate")
        print(f"\n@{'ABMX' if e['op'] == 'xor' else 'ABM'}  i{e['width']}  "
              f"count={e['count']}  min={verdict}")
        print(f"    matrix={e['matrix'][:26]}{'...' if len(e['matrix']) > 26 else ''}"
              f"  mask={e['mask']}")
        for s in e.get("sequence", []):
            print(f"      {s}")
        if "proof" in e:
            print(f"    alive-tv: {e['proof']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
