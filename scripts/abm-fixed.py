#!/usr/bin/env python3
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
"""
Which fixed affine bit operations deserve a hardwired instruction?

abm-report.py asks how a general bit-matrix instruction would fare. This asks
a narrower question that hardware designers can act on: is there a specific
matrix (M, b), with its constants baked into the circuit, that programs
compute often enough, on hot enough paths, and expensively enough on today's
ISAs, to earn an opcode of its own? Such an instruction needs no matrix
operand and no table: a permutation is only wires, and an XOR-linear map is a
small XOR tree.

Each distinct operation (op, input widths, M, b) is one candidate. For each
one this reports:

  cost     instructions on x86-64, AArch64 and RISC-V: the abm-isa.py oracle,
           capped by what llc emits when an --llc memo has the region
  save     cost - 1 on each ISA for a fixed instruction reading at most two
           registers (k - 1 for k > 2 inputs)
  gates    2-input XOR/OR gates and logic depth of the fixed circuit; 0 for
           a pure wiring permutation
  static   projects, sites, and sites inside loops (llvm-opt-benchmark etc.)
  dynamic  for profiled corpora (records with "count"): the share of each
           program's executed IR instructions the fixed instruction would
           save, i.e. sum(count * save) / sum(dyn_insts) of the program

Candidates are ranked by how many programs they would help, statically and
dynamically, and each one comes with source locations to inspect. A second
table groups operations into families by shape (the region's opcode
histogram), which is where an instruction with a small immediate (a shift
amount) would come from.

  abm-fixed.py [--llc llc.jsonl] [--verified proof.jsonl] \\
               [--samples shortlist.jsonl] [--json out.json] census.jsonl...

--samples writes one record per shortlisted operation, for abm-llc.py and
abm-verify.py; run those on it, then rerun with the memos.
"""

import argparse
import collections
import re
import importlib.util
import json
import math
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name,
                                                  os.path.join(_here, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


isa = _load("abm_isa", "abm-isa.py")
ver = _load("abm_verify", "abm-verify.py")
llcm = _load("abm_llc", "abm-llc.py")

ISAS = isa.ISAS


def project_of(src):
    parts = (src or "").split("/")
    if "bench" in parts:
        i = parts.index("bench")
        if i + 1 < len(parts):
            return parts[i + 1]
    return parts[-2] if len(parts) >= 2 else (src or "?")


_RUST_HASH = re.compile(r"17h[0-9a-f]{16}E|\.llvm\.\d+")


def origin_of(fn):
    """The enclosing function with Rust's per-crate hashes removed, so the
    same library function inlined into many projects counts once."""
    return _RUST_HASH.sub("", fn or "")


def key_of(r):
    ins = tuple(i["w"] for i in r.get("inputs", []))
    return (r.get("op"), r["width"], ins, r["matrix"], r["mask"])


class Cand:
    __slots__ = ("rec", "sites", "loop_sites", "proj", "dyn", "crit",
                 "size", "locs", "ops", "cuts", "origins")

    def __init__(self, rec):
        self.rec = rec          # first record seen, with its cut
        self.sites = 0
        self.loop_sites = 0
        self.proj = collections.Counter()      # project -> static sites
        self.dyn = collections.Counter()       # project -> executions
        self.crit = 0
        self.size = 1 << 30
        self.locs = collections.Counter()      # (project, loc, fn) -> weight
        self.ops = ""
        self.cuts = set()                      # llc memo keys of a few cuts
        self.origins = set()                   # distinct enclosing functions


def load(paths):
    cands = {}
    dyn_insts = collections.Counter()   # project -> executed IR instructions
    profiled = set()
    n = 0
    for p in paths:
        with open(p) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                kind = r.get("kind")
                if kind == "func":
                    pr = project_of(r.get("source"))
                    dyn_insts[pr] += int(r.get("dyn_insts", 0))
                    profiled.add(pr)
                    continue
                if kind != "census":
                    continue
                n += 1
                k = key_of(r)
                c = cands.get(k)
                if c is None:
                    c = cands[k] = Cand(r)
                    c.ops = r.get("region_ops", "")
                pr = project_of(r.get("source"))
                c.sites += 1
                c.proj[pr] += 1
                if int(r.get("loop_depth", 0)) > 0:
                    c.loop_sites += 1
                cnt = int(r.get("count", 0))
                if cnt:
                    c.dyn[pr] += cnt
                c.crit = max(c.crit, int(r.get("crit", 0)))
                if len(c.origins) < 1000:
                    c.origins.add(origin_of(r.get("function")))
                c.size = min(c.size, int(r.get("region_size", 0)))
                w = cnt if cnt else float(r.get("rel_freq", 1.0))
                c.locs[(pr, r.get("loc") or "", r.get("function", ""))] += w
                if len(c.cuts) < 4 and "cut" in r:
                    c.cuts.add(llcm.cut_key(r["cut"]))
    return n, cands, dyn_insts, profiled


def gates(rec):
    """2-input gates and depth of the fixed circuit computing M*x ^ b."""
    a = isa.Affine(rec)
    # identical rows are one circuit fanned out (x != 0 broadcast to a mask)
    pcs = [bin(r).count("1") for r in set(a.rows)]
    g = sum(max(0, p - 1) for p in pcs)
    d = max((math.ceil(math.log2(p)) for p in pcs if p > 1), default=0)
    return g, d


def perm_shape(rec):
    """For a matrix with at most one bit per row: 'pext M' (gather the bits
    of M to the bottom), 'pdep M' (scatter the bottom bits to M), 'pext+pdep'
    (monotone, neither end contiguous) or 'shuffle'. The inputs are taken as
    one concatenated word. None for other matrices."""
    a = isa.Affine(rec)
    mp = a.single()
    if mp is None:
        return None
    pairs = [(o, j) for o, j in enumerate(mp) if j is not None]
    if len(pairs) < 2:
        return None
    js = [j for _, j in pairs]
    os_ = [o for o, _ in pairs]
    if any(x >= y for x, y in zip(js, js[1:])):
        return "shuffle"
    n = len(pairs)
    if os_ == list(range(n)):
        return f"pext {hex(sum(1 << j for j in js))}"
    if js == list(range(n)):
        return f"pdep {hex(sum(1 << o for o in os_))}"
    return "pext+pdep"


def n_inputs(rec):
    return len(isa.Affine(rec).used_inputs())


def describe(rec):
    try:
        return isa.classify(rec)
    except Exception:
        return "?"


def evaluate(cands, llc_memo, proof_memo):
    """Per candidate: costs, savings, gates; drops refuted ones."""
    out = []
    for k, c in cands.items():
        r = c.rec
        if proof_memo:
            v = proof_memo.get(ver.record_key(r))
            if v and v != "verified" and v not in ("timeout", "error"):
                continue
        try:
            ic = isa.isa_costs(r)
        except Exception:
            continue
        costs = {i: v[0] for i, v in ic.items()}
        hows = {i: v[1] for i, v in ic.items()}
        llc = {}
        for ck in c.cuts:
            for i, x in (llc_memo.get(ck) or {}).items():
                if x is not None:
                    llc[i] = min(x, llc.get(i, x))
        for i, x in llc.items():
            costs[i] = min(costs[i], x)
        ni = n_inputs(r)
        hw = 1 if ni <= 2 else ni - 1
        save = {i: max(0, costs[i] - hw) for i in ISAS}
        g, d = gates(r)
        shape = perm_shape(r)
        out.append(dict(key=k, c=c, costs=costs, hows=hows, save=save,
                        inputs=ni, gates=g, depth=d, cls=describe(r),
                        shape=shape,
                        llc=bool(llc),
                        proof=(proof_memo or {}).get(ver.record_key(r))))
    return out


def dyn_share(e, dyn_insts, i):
    """{project: share of its executed instructions saved on ISA i}."""
    s = {}
    for pr, cnt in e["c"].dyn.items():
        tot = dyn_insts.get(pr, 0)
        if tot:
            s[pr] = cnt * e["save"][i] / tot
    return s


def fmt_pct(x):
    return f"{100 * x:.3g}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--llc")
    ap.add_argument("--verified")
    ap.add_argument("--samples", help="write shortlisted records here")
    ap.add_argument("--json", help="write the ranked candidates here")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min-save", type=int, default=2,
                    help="instructions saved on an ISA to count as a win")
    a = ap.parse_args()

    llc_memo = {}
    if a.llc and os.path.exists(a.llc):
        with open(a.llc) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    llc_memo[e["key"]] = e["llc"]
                except (ValueError, KeyError):
                    pass
    proof_memo = {}
    if a.verified and os.path.exists(a.verified):
        with open(a.verified) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    proof_memo[e["key"]] = e["verdict"]
                except (ValueError, KeyError):
                    pass

    n, cands, dyn_insts, profiled = load(a.logs)
    print(f"{n} sites, {len(cands)} distinct operations, "
          f"{len(profiled)} profiled programs", file=sys.stderr)
    ev = evaluate(cands, llc_memo, proof_memo)
    # A fixed instruction reads one or two registers and writes a value; an
    # i1 result is a condition, which compare-and-branch already absorbs, and
    # a pack of many registers is a field-insert sequence, not one matrix.
    ev = [e for e in ev if e["c"].rec["width"] > 1 and e["inputs"] <= 2]

    # shortlist: saves something somewhere and is not a one-off
    def worth(e):
        c = e["c"]
        return max(e["save"].values()) >= 1 and \
            (len(c.proj) >= 2 or sum(c.dyn.values()) > 0)

    if a.samples:
        with open(a.samples, "w") as f:
            for e in ev:
                if worth(e):
                    f.write(json.dumps(e["c"].rec) + "\n")
        print(f"wrote {sum(1 for e in ev if worth(e))} samples to "
              f"{a.samples}", file=sys.stderr)

    W = a.min_save
    for e in ev:
        c = e["c"]
        e["static_proj"] = len(c.proj)
        e["dyn"] = {i: dyn_share(e, dyn_insts, i) for i in ISAS}
        e["dyn_max"] = {i: max(e["dyn"][i].values(), default=0.0)
                        for i in ISAS}
        e["dyn_progs"] = {i: sum(1 for v in e["dyn"][i].values() if v >= 1e-3)
                          for i in ISAS}

    def row(e):
        c = e["c"]
        top = c.locs.most_common(2)
        where = "; ".join(f"{p}:{l or f}" for (p, l, f), _ in top)
        cost = "/".join(str(e["costs"][i]) for i in ISAS)
        dm = "/".join(fmt_pct(e["dyn_max"][i]) for i in ISAS)
        cls = e["shape"] or e["cls"]
        return (f"{cls[:24]:24} {c.rec['width']:>3}<-"
                f"{'+'.join(str(i['w']) for i in c.rec.get('inputs', [])):9}"
                f" in={e['inputs']} cost x/a/r={cost:9} gates={e['gates']:>4}"
                f"/d{e['depth']} crit={c.crit:>2} proj={len(c.proj):>4} "
                f"orig={len(c.origins):>4} sites={c.sites:>6} loop={c.loop_sites:>5} "
                f"dynmax={dm:20} {'L' if e['llc'] else ' '}"
                f"{(e['proof'] or '-')[:1]} | {c.ops[:40]} | {where[:110]}")

    print("\n== Expensive everywhere: saving >= 3 on every ISA, by projects")
    sel = [e for e in ev if min(e["save"].values()) >= 3]
    for e in sorted(sel, key=lambda e: -e["static_proj"])[:a.top]:
        print(row(e))

    print(f"\n== Most widespread operations saving >= {W} on every ISA "
          f"(static projects)")
    sel = [e for e in ev if min(e["save"].values()) >= W]
    for e in sorted(sel, key=lambda e: -e["static_proj"])[:a.top]:
        print(row(e))
    for i in ISAS:
        print(f"\n== Most widespread operations saving >= {W} on {i}")
        sel = [e for e in ev if e["save"][i] >= W]
        for e in sorted(sel, key=lambda e: -e["static_proj"])[:a.top]:
            print(row(e))
    if profiled:
        for i in ISAS:
            print(f"\n== Hottest on {i} (saving >= {W}): most programs where "
                  f"it saves >= 0.1% of executed instructions, then the "
                  f"largest share")
            sel = [e for e in ev if e["dyn_max"][i] > 0 and e["save"][i] >= W]
            for e in sorted(sel, key=lambda e: (-e["dyn_progs"][i],
                                                -e["dyn_max"][i]))[:a.top]:
                print(row(e))

    # families: operations with the same opcode shape, e.g. every x ^ (x >> k)
    fam = collections.defaultdict(list)
    for e in ev:
        r = e["c"].rec
        fam[(e["c"].ops, r.get("op"), r["width"], e["inputs"])].append(e)
    print(f"\n== Families (same opcode shape) saving >= {W} on RISC-V, by "
          f"projects")
    rows = []
    for k, es in fam.items():
        es = [e for e in es if e["save"]["rv"] >= W]
        if not es:
            continue
        projs = set()
        for e in es:
            projs |= set(e["c"].proj)
        dynp = set()
        for e in es:
            dynp |= {p for p, v in e["dyn"]["rv"].items() if v >= 1e-3}
        rows.append((len(projs), len(dynp), len(es), k, es))
    for np_, nd, ne, k, es in sorted(rows, key=lambda t: (-t[0], -t[1]))[
            :a.top]:
        best = max(es, key=lambda e: e["static_proj"])
        print(f"proj={np_:>4} hotprogs={nd:>3} members={ne:>4} "
              f"{k[1]} w={k[2]} in={k[3]} | {k[0][:60]} | e.g. {row(best)[:90]}")

    if a.json:
        with open(a.json, "w") as f:
            for e in sorted(ev, key=lambda e: -e["static_proj"]):
                if max(e["save"].values()) < 1:
                    continue
                c = e["c"]
                f.write(json.dumps(dict(
                    rec={k: v for k, v in c.rec.items()},
                    cls=e["cls"], shape=e["shape"], inputs=e["inputs"],
                    costs=e["costs"],
                    hows=e["hows"], save=e["save"], gates=e["gates"],
                    depth=e["depth"], crit=c.crit, size=c.size,
                    sites=c.sites, loop_sites=c.loop_sites,
                    origins=len(c.origins),
                    projects=dict(c.proj), dyn=dict(c.dyn),
                    dyn_share=e["dyn"], ops=c.ops, proof=e["proof"],
                    locs=[[p, l, fn, w] for (p, l, fn), w in
                          c.locs.most_common(8)])) + "\n")


if __name__ == "__main__":
    main()
