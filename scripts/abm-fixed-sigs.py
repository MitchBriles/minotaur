#!/usr/bin/env python3
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
"""
Finds named fixed operations in abm-fixed.py --json output by their exact
function on bits: MT19937 tempering, zigzag coding, the fill-below-top-bit
smear, Morton spread/compress, RISC-V immediate decoding. Prints, per
operation and per run, the projects, sites, sites in loops, ISA costs and the
largest dynamic shares, then per family the union of projects in "static".

  abm-fixed-sigs.py scripts/abm-isa.py runs/static/fixed.json runs/pgo/fixed.json

A run is named by the directory of its fixed.json.
"""
import json, sys, importlib.util, collections
spec = importlib.util.spec_from_file_location("isa", sys.argv[1]); isa = importlib.util.module_from_spec(spec); spec.loader.exec_module(isa)
def M(w): return (1 << w) - 1
def sar(x, s, w): return ((x | (-(x >> (w - 1)) << w)) >> s) & M(w) if x >> (w-1) else x >> s
def rows_of(f, win, wout):
    rows = [0]*wout; b = f(0)
    for j in range(win):
        c = f(1 << j) ^ b
        for i in range(wout):
            if c >> i & 1: rows[i] |= 1 << j
    return tuple(rows), b
def spread(x, k, n):  # bit i -> bit k*i
    return sum(((x >> i) & 1) << (k*i) for i in range(n))
def compress(x, k, n):
    return sum(((x >> (k*i)) & 1) << i for i in range(n))
SIGS = {}
for w in (32, 64):
    SIGS[f"zigzag-decode{w}"] = (lambda w: (lambda x: (x >> 1) ^ ((-(x & 1)) & M(w))))(w), w, w
    SIGS[f"zigzag-encode{w}"] = (lambda w: (lambda x: ((x << 1) & M(w)) ^ sar(x, w-1, w)))(w), w, w
    SIGS[f"smear{w}"] = (lambda w: (lambda x: (lambda y: y)(__import__('functools').reduce(lambda y, s: y | (y >> s), [1,2,4,8,16,32][:5 if w==32 else 6], x))))(w), w, w
SIGS["mt32-temper"] = (lambda y: (lambda y: (lambda y: (lambda y: y ^ (y >> 18))(y ^ ((y << 15) & 0xefc60000)))(y ^ ((y << 7) & 0x9d2c5680)))(y ^ (y >> 11))), 32, 32
SIGS["mt64-temper"] = (lambda y: (lambda y: (lambda y: (lambda y: y ^ (y >> 43))(y ^ ((y << 37) & 0xfff7eee000000000)))(y ^ ((y << 17) & 0x71d67fffeda60000)))(y ^ ((y >> 29) & 0x5555555555555555))), 64, 64
def _mt32_in64(x):
    x ^= (x >> 11) & 0xffffffff; x ^= (x << 7) & 0x9d2c5680; x ^= (x << 15) & 0xefc60000
    return (x ^ (x >> 18)) & M(64)
SIGS["mt32-temper"+"-in64"] = _mt32_in64, 64, 64
SIGS["mt64-temper-trunc32"] = (lambda y: SIGS["mt64-temper"][0](y) & M(32)), 64, 32
SIGS["morton2-spread16"] = (lambda x: spread(x, 2, 16)), 16, 32
SIGS["morton2-spread32"] = (lambda x: spread(x, 2, 32)), 32, 64
SIGS["morton2-compress32"] = (lambda x: compress(x, 2, 16)), 32, 16
SIGS["morton3-spread21"] = (lambda x: spread(x, 3, 21)), 21, 64
SIGS["morton3-spread10"] = (lambda x: spread(x, 3, 10)), 10, 32
def bits(x, hi, lo): return (x >> lo) & ((1 << (hi - lo + 1)) - 1)
def rv_s(i): return (bits(i,31,25) << 5) | bits(i,11,7)
def rv_b(i): return (bits(i,31,31) << 12) | (bits(i,7,7) << 11) | (bits(i,30,25) << 5) | (bits(i,11,8) << 1)
def rv_j(i): return (bits(i,31,31) << 20) | (bits(i,19,12) << 12) | (bits(i,20,20) << 11) | (bits(i,30,21) << 1)
def sx(v, b, w): return (v | (M(w) ^ M(b))) if v >> (b - 1) & 1 else v
for nm, f, b in (("rv-imm-S", rv_s, 12), ("rv-imm-B", rv_b, 13), ("rv-imm-J", rv_j, 21)):
    SIGS[nm] = f, 32, 32
    SIGS[nm + "-sext32"] = (lambda f, b: (lambda i: sx(f(i), b, 32)))(f, b), 32, 32
    SIGS[nm + "-sext64"] = (lambda f, b: (lambda i: sx(f(i), b, 64)))(f, b), 32, 64
TARGET = {}
for name, (f, wi, wo) in SIGS.items():
    TARGET[rows_of(f, wi, wo)] = name

def norm(rec):
    """Rows over the used low bits of a single input, ignoring known-zero high input bits."""
    a = isa.Affine(rec)
    if len(a.widths) != 1: return []
    out = []
    for wi in sorted({a.widths[0], 10, 16, 21, 32, 64}):
        if wi > a.widths[0]: continue
        if any(r >> wi for r in a.rows): continue
        out.append(((tuple(a.rows), a.b), wi))
    return out
res = collections.defaultdict(lambda: dict(ops=0, sites=0, loop=0, projects=set(), dyn=collections.Counter(), share=collections.defaultdict(float), costs=None, locs=[]))
for path in sys.argv[2:]:
    for line in open(path):
        e = json.loads(line)
        for key, wi in norm(e["rec"]):
            name = TARGET.get(key)
            if not name: continue
            g = res[(name, path.split("/")[-2])]
            g["ops"] += 1; g["sites"] += e["sites"]; g["loop"] += e["loop_sites"]; g["projects"] |= set(e["projects"])
            for p, c in e["dyn"].items(): g["dyn"][p] += c
            for i in ("x86", "arm", "rv"):
                for p, v in e["dyn_share"][i].items(): g["share"][(i, p)] = max(g["share"][(i, p)], v)
            g["costs"] = e["costs"] if g["costs"] is None else {i: min(g["costs"][i], e["costs"][i]) for i in e["costs"]}
            g["locs"] += e["locs"][:2]
            break
fam = collections.defaultdict(set); famsites = collections.Counter(); famloop = collections.Counter()
for (name, run), g in res.items():
    if run != "static": continue
    f = "rvimm" if name.startswith("rv-imm") else "mt" if name.startswith("mt") else name.rsplit("-",1)[0].rstrip("0123456789") if not name.startswith("zigzag") else "zigzag"
    fam[f] |= g["projects"]; famsites[f] += g["sites"]; famloop[f] += g["loop"]
for f in fam: print("FAMILY", f, "projects", len(fam[f]), "sites", famsites[f], "loop", famloop[f], sorted(fam[f]))
for (name, run), g in sorted(res.items()):
    top = sorted(((v, i, p) for (i, p), v in g["share"].items()), reverse=True)[:3]
    print(f"{name:22} {run:7} ops={g['ops']:3} sites={g['sites']:5} loop={g['loop']:5} projects={len(g['projects']):3} costs={g['costs']} hot={[(p,i,round(v*100,3)) for v,i,p in top]}")
    print("      ", sorted(g["projects"])[:30])
    print("      ", [l[:2] for l in g["locs"][:4]])
