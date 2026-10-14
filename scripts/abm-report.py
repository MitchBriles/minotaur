#!/usr/bin/env python3
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
"""
Aggregates census logs into the answer to "which affine bit manipulations do
programs perform, and which of them are not already one instruction?".

Records are grouped by the operation itself, (op, input widths, matrix,
mask), so the same operation in different programs and functions is one row.
Each group is classified and costed on x86-64, AArch64 and RISC-V by
abm-isa.py. The report shows:

  * the class distribution (sites, distinct operations, projects);
  * ISA coverage: the share of sites that need <= 1, 2, or >= 3 instructions
    on each ISA and on the best of them;
  * what an ABM would save: (best ISA cost) - (ABM cost), where an ABM reads
    at most two registers, so a region with k inputs costs max(1, k - 1);
  * the residue: operations an ABM would do in --min-cost - 1 fewer
    instructions than the best ISA,
    ranked by how many projects use them;
  * the most widespread small lookup tables (kind "table").

  abm-report.py census.jsonl [--verified memo.jsonl] [--min-projects 2]
"""

import argparse
import collections
import importlib.util
import json
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


def project_of(src):
    parts = (src or "").split("/")
    if "bench" in parts:
        i = parts.index("bench")
        if i + 1 < len(parts):
            return parts[i + 1]
    if len(parts) >= 2:
        return parts[-2] or parts[0]
    return src or "?"


class Group:
    __slots__ = ("rec", "sites", "projects", "functions", "freq", "depth",
                 "ops", "sizes", "verdicts", "froms")

    def __init__(self, rec):
        self.rec = rec
        self.sites = 0
        self.projects = collections.Counter()
        self.functions = collections.Counter()
        self.freq = 0.0
        self.depth = 0
        self.ops = collections.Counter()
        self.sizes = []
        self.verdicts = collections.Counter()
        self.froms = collections.Counter()


def key_of(r):
    ins = tuple(i["w"] for i in r.get("inputs", []))
    if r["kind"] == "table":
        return ("table", r["width"], ins, r["table"])
    return (r.get("op"), r["width"], ins, r["matrix"], r["mask"])


def load(paths, memo):
    groups = {}
    n = 0
    for p in paths:
        with open(p) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") not in ("census", "table"):
                    continue
                n += 1
                k = key_of(r)
                g = groups.get(k)
                if g is None:
                    g = groups[k] = Group(r)
                g.sites += 1
                g.projects[project_of(r.get("source"))] += 1
                g.functions[(project_of(r.get("source")),
                             r.get("function"))] += 1
                g.freq += float(r.get("rel_freq", 1.0))
                g.depth = max(g.depth, int(r.get("loop_depth", 0)))
                g.ops[r.get("region_ops", "")] += 1
                g.sizes.append(r.get("region_size", 0))
                g.froms[",".join(i.get("from", "?")
                                 for i in r.get("inputs", []))] += 1
                if memo and r["kind"] == "census":
                    g.verdicts[memo.get(ver.record_key(r), "unproved")] += 1
    return n, groups


def bucket(n):
    return "<=1" if n <= 1 else "2" if n == 2 else ">=3"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--verified",
                    help="abm-verify.py memo: refuted operations are dropped "
                         "(random screening can miss carries that rarely "
                         "propagate), and the rest are annotated")
    ap.add_argument("--proved-only", action="store_true",
                    help="with --verified, keep only operations proved at "
                         "some site")
    ap.add_argument("--llc", help="abm-llc.py memo: add what llc emits today")
    ap.add_argument("--min-projects", type=int, default=2)
    ap.add_argument("--min-cost", type=int, default=3,
                    help="residue threshold, in instructions on the best ISA")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--json", action="store_true",
                    help="emit the residue as JSON lines instead of text")
    a = ap.parse_args()

    memo = {}
    if a.verified:
        with open(a.verified) as f:
            for line in f:
                e = json.loads(line)
                memo[e["key"]] = e["verdict"].split(":")[0]

    llc = {}
    if a.llc:
        with open(a.llc) as f:
            for line in f:
                e = json.loads(line)
                llc[e["key"]] = e["llc"]

    n, groups = load(a.logs, memo)
    census = {k: g for k, g in groups.items() if k[0] != "table"}
    if memo:
        dropped = [k for k, g in census.items() if g.verdicts["refuted"]]
        for k in dropped:
            del census[k]
        if a.proved_only:
            for k in [k for k, g in census.items()
                      if not g.verdicts["verified"]]:
                del census[k]
        if not a.json:
            print(f"# dropped {len(dropped)} refuted operation(s)")
    tables = {k: g for k, g in groups.items() if k[0] == "table"}
    projects = set()
    for g in groups.values():
        projects |= set(g.projects)

    # cost and class once per distinct operation; what llc emits is a real
    # implementation, so it caps the oracle's estimate
    info = {}
    for k, g in census.items():
        c = dict(isa.isa_costs(g.rec))
        lc = llc.get(llcm.cut_key(g.rec.get("cut", "")))
        if lc:
            for i in isa.ISAS:
                if lc.get(i) is not None and lc[i] < c[i][0]:
                    c[i] = (lc[i], "as llc emits it")
        best = min(v[0] for v in c.values())
        nin = len(g.rec.get("inputs", [])) or 1
        abm = max(1, nin - 1)
        info[k] = (isa.classify(g.rec), c, best, abm, max(best - abm, 0))

    if not a.json:
        print(f"# {n} records, {len(census)} distinct affine operations, "
              f"{len(tables)} distinct tables, {len(projects)} projects\n")

        # class distribution
        cls = collections.defaultdict(lambda: [0, 0, set(), 0])
        for k, g in census.items():
            c = cls[info[k][0]]
            c[0] += g.sites
            c[1] += 1
            c[2] |= set(g.projects)
            if info[k][4] >= a.min_cost - 1:
                c[3] += g.sites
        print(f"{'class':22} {'sites':>7} {'distinct':>8} {'projects':>8} "
              f"{'sites ABM saves >=' + str(a.min_cost - 1):>22}")
        for name, (s, d, p, hard) in sorted(cls.items(),
                                            key=lambda e: -e[1][0]):
            print(f"{name:22} {s:7} {d:8} {len(p):8} {hard:22}")

        # ISA coverage, by sites
        total = sum(g.sites for g in census.values()) or 1
        print(f"\nISA coverage, share of {total} sites by instructions needed:")
        print(f"{'':8} {'<=1':>7} {'2':>7} {'>=3':>7}")
        for name in isa.ISAS + ("best",):
            b = collections.Counter()
            for k, g in census.items():
                cost = info[k][2] if name == "best" else info[k][1][name][0]
                b[bucket(cost)] += g.sites
            print(f"{name:8} " + " ".join(f"{100 * b[x] / total:6.1f}%"
                                           for x in ("<=1", "2", ">=3")))

        # the same with every project weighted equally, so that one large
        # code base (cvc5 alone is a quarter of the sites) cannot set it
        psites = collections.Counter()
        for g in census.values():
            psites.update(g.projects)
        print(f"\nISA coverage, each of {len(psites)} projects weighted "
              f"equally:")
        print(f"{'':8} {'<=1':>7} {'2':>7} {'>=3':>7} {'ABM saves>=2':>13}")
        for name in isa.ISAS + ("best",):
            b = collections.Counter()
            for k, g in census.items():
                cost = info[k][2] if name == "best" else info[k][1][name][0]
                for p, n in g.projects.items():
                    w = n / psites[p] / len(psites)
                    b[bucket(cost)] += w
                    if name == "best" and info[k][4] >= a.min_cost - 1:
                        b["saves"] += w
            extra = f" {100 * b['saves']:12.1f}%" if name == "best" else ""
            print(f"{name:8} " + " ".join(f"{100 * b[x]:6.1f}%"
                                           for x in ("<=1", "2", ">=3"))
                  + extra)

        if llc:
            print(f"\nCompiled today (llc -O2), share of sites by instructions"
                  f" emitted:")
            print(f"{'':8} {'<=1':>7} {'2':>7} {'>=3':>7} {'unknown':>8}")
            for name in isa.ISAS + ("best",):
                b = collections.Counter()
                for k, g in census.items():
                    c = llc.get(llcm.cut_key(g.rec.get("cut", "")))
                    if not c or any(v is None for v in c.values()):
                        b["unknown"] += g.sites
                        continue
                    v = min(c.values()) if name == "best" else c[name]
                    b[bucket(v)] += g.sites
                print(f"{name:8} " + " ".join(
                    f"{100 * b[x] / total:6.1f}%" for x in ("<=1", "2", ">=3"))
                    + f" {100 * b['unknown'] / total:7.1f}%")

        # what one ABM instruction would save over the best existing ISA:
        # (best cost - 1) per site, statically and weighted by how often the
        # block runs relative to its function's entry
        sav = collections.defaultdict(lambda: [0, 0.0, 0, 0.0])
        for k, g in census.items():
            gain = info[k][4]
            sav[info[k][0]][0] += gain * g.sites
            sav[info[k][0]][1] += gain * g.freq
            # a 2-instruction sequence is what macro-op fusion already
            # covers; count separately what only an ABM would collapse
            if gain >= a.min_cost - 1:
                sav[info[k][0]][2] += gain * g.sites
                sav[info[k][0]][3] += gain * g.freq
        ts = sum(v[0] for v in sav.values()) or 1
        tf = sum(v[1] for v in sav.values()) or 1
        print(f"\nInstructions an ABM would save over the best ISA "
              f"(best cost - max(1, inputs - 1), summed over sites):")
        print(f"{'class':28} {'static':>9} {'share':>6} {'freq-wtd':>11} "
              f"{'share':>6} {'from saves>=' + str(a.min_cost - 1) + ': static':>20}"
              f" {'freq-wtd':>11}")
        for name, (st, fq, st3, fq3) in sorted(sav.items(),
                                               key=lambda e: -e[1][1]):
            if st:
                print(f"{name:28} {st:9} {100 * st / ts:5.1f}% {fq:11.1f} "
                      f"{100 * fq / tf:5.1f}% {st3:20} {fq3:11.1f}")
        print(f"{'total':28} {ts:9} {'':6} {tf:11.1f} {'':6} "
              f"{sum(v[2] for v in sav.values()):20} "
              f"{sum(v[3] for v in sav.values()):11.1f}")
        print(f"per site: {ts / total:.3f} static, over {total} sites")

    # the residue
    res = [(k, g) for k, g in census.items()
           if info[k][4] >= a.min_cost - 1 and len(g.projects) >= a.min_projects]
    res.sort(key=lambda e: (-len(e[1].projects), -e[1].sites,
                            -info[e[0]][2]))
    if a.json:
        for k, g in res:
            cl, c, best = info[k][:3][:3]
            print(json.dumps({
                "class": cl, "op": g.rec.get("op"), "width": g.rec["width"],
                "inputs": [i["w"] for i in g.rec.get("inputs", [])],
                "matrix": g.rec["matrix"], "mask": g.rec["mask"],
                "sites": g.sites, "projects": dict(g.projects),
                "freq": round(g.freq, 3), "max_loop_depth": g.depth,
                "gain": info[k][4], "best": best, "abm_cost": info[k][3],
                "costs": {i: c[i][0] for i in c},
                "how": {i: c[i][1] for i in c},
                "functions": [f"{p}:{fn}" for (p, fn), _ in
                              g.functions.most_common(8)],
                "region_ops": g.ops.most_common(1)[0][0],
                "verdicts": dict(g.verdicts),
                "llc": llc.get(llcm.cut_key(g.rec.get("cut", ""))),
                "cut": g.rec.get("cut")}))
        return

    print(f"\n## Residue: an ABM saves >= {a.min_cost - 1} instructions over "
          f"every ISA, in >= {a.min_projects} projects ({len(res)} operations)\n")
    for rank, (k, g) in enumerate(res[:a.top], 1):
        cl, c, best = info[k][:3]
        r = g.rec
        ins = "+".join(str(i["w"]) for i in r.get("inputs", []))
        costs = "  ".join(f"{i}={c[i][0]} ({c[i][1]})" for i in isa.ISAS)
        print(f"#{rank} {cl}  {r.get('op')}  i{ins} -> i{r['width']}  "
              f"sites={g.sites} projects={len(g.projects)} "
              f"maxdepth={g.depth}")
        print(f"    {costs}")
        lc = llc.get(llcm.cut_key(r.get("cut", "")))
        if lc:
            print("    llc today: " + "  ".join(f"{i}={lc[i]}"
                                                for i in isa.ISAS))
        print(f"    projects: " + ", ".join(f"{p}({n})" for p, n in
                                         g.projects.most_common(8)))
        print(f"    e.g.: " + ", ".join(f"{p}:{fn}" for (p, fn), _ in
                                     g.functions.most_common(4)))
        print(f"    region: {g.ops.most_common(1)[0][0]}  inputs from: "
              f"{g.froms.most_common(1)[0][0]}")
        if g.verdicts:
            print(f"    proof: {dict(g.verdicts)}")
        print(f"    matrix={r['matrix'][:64]}{'...' if len(r['matrix']) > 64 else ''}"
              f" mask={r['mask']}")

    print(f"\n## Lookup tables in >= {a.min_projects} projects\n")
    tl = [(k, g) for k, g in tables.items() if len(g.projects) >= a.min_projects]
    tl.sort(key=lambda e: (-len(e[1].projects), -e[1].sites))
    for k, g in tl[:a.top // 2]:
        r = g.rec
        print(f"  {r['table_bits']} free bit(s) -> i{r['width']}  "
              f"sites={g.sites} projects={len(g.projects)}  "
              f"{g.ops.most_common(1)[0][0]}  e.g. " +
              ", ".join(f"{p}:{fn}" for (p, fn), _ in
                        g.functions.most_common(3)))


if __name__ == "__main__":
    main()
