#!/usr/bin/env python3
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
"""
How many instructions does an existing ISA need for an ABM matrix?

This is the "is it already an instruction?" oracle. Each census record is an
affine map out = M*x ^ b over the concatenation x of a region's inputs. The
recognizers below name the matrix families that x86-64, AArch64 and RISC-V
implement directly, and a fallback costs a generic shift/mask/combine network
per ISA, so every matrix gets an upper bound on each ISA.

Profiles (all scalar code, vector units used only where a single scalar
operation is impossible, counting the moves in and out):

  x86   x86-64-v4 + BMI1/2, GFNI, PCLMULQDQ, AVX-512 VBMI/BITALG
  arm   Armv9-A + SVE2-BITPERM, CRC, PMULL; shifted-register operands
  rv    RVA23: Zba/Zbb/Zbs, Zbkb, Zbkx, Zbc

Constants are free: masks and matrices in a hot loop are hoisted, so only the
operations are counted. This is deliberately generous to existing ISAs; a
matrix that still costs several instructions everywhere is a real candidate.

Matrix layout (as written by the census): bit i*In + j of M is row i, input
bit j, and row i produces output bit Out-1-i. Input k occupies bits
[off_k, off_k + w_k) of x, the first input lowest.
"""

import sys

ISAS = ("x86", "arm", "rv")


class Affine:
    """A census record decoded into per-output-bit row masks."""

    def __init__(self, rec):
        self.out = rec["width"]
        self.inn = rec.get("in_width", self.out)
        ins = rec.get("inputs") or [{"w": self.inn}]
        self.widths = [i["w"] for i in ins]
        self.offs = []
        o = 0
        for w in self.widths:
            self.offs.append(o)
            o += w
        self.op = rec.get("op", "or")
        self.b = int(rec.get("mask") or "0", 16)
        m = int(rec["matrix"], 16)
        self.rows = [0] * self.out        # indexed by output bit, LSB = 0
        for i in range(self.out):
            r = (m >> (i * self.inn)) & ((1 << self.inn) - 1)
            self.rows[self.out - 1 - i] = r

    # --- structure -------------------------------------------------------
    def single(self):
        """Output bit -> input bit (or None) when every row has <= 1 bit."""
        mp = []
        for r in self.rows:
            if r & (r - 1):
                return None
            mp.append(r.bit_length() - 1 if r else None)
        return mp

    def input_of(self, j):
        for k, (o, w) in enumerate(zip(self.offs, self.widths)):
            if o <= j < o + w:
                return k, j - o
        raise ValueError(j)

    def used_inputs(self):
        used = set()
        for r in self.rows:
            j = 0
            while r:
                if r & 1:
                    used.add(self.input_of(j)[0])
                r >>= 1
                j += 1
        return sorted(used)

    def diagonals(self):
        """{(input k, d): output mask} with d = out bit - bit within input."""
        g = {}
        for o, r in enumerate(self.rows):
            j = 0
            while r:
                if r & 1:
                    k, jj = self.input_of(j)
                    g[(k, o - jj)] = g.get((k, o - jj), 0) | (1 << o)
                r >>= 1
                j += 1
        return g


def ones(n):
    return (1 << n) - 1


def contiguous(mask):
    if not mask:
        return False
    m = mask >> ((mask & -mask).bit_length() - 1)
    return m & (m + 1) == 0


def full_mask(a, k, d):
    """Output bits a shift of input k by d can reach at all."""
    w = a.widths[k]
    src = ones(w)
    sh = src << d if d >= 0 else src >> -d
    return sh & ones(a.out)


# --- recognizers: each returns {isa: (count, how)} for what it matches -----

def sext_field(a, mp):
    """(field_lo, k, lo, top) when the outputs are a sign extension of a k-bit
    field starting at input bit field_lo, placed at output bit lo, with its
    sign repeated up to output bit top and zeros everywhere else."""
    pm = {o: j for o, j in enumerate(mp) if j is not None}
    if not pm:
        return None
    top = max(pm)
    jt = pm[top]
    o = top
    while o - 1 in pm and pm[o - 1] == jt:
        o -= 1
    run_lo = o
    if run_lo == top:
        return None
    d = run_lo - jt
    lo = run_lo
    while lo - 1 in pm and pm[lo - 1] == lo - 1 - d:
        lo -= 1
    if set(pm) != set(range(lo, top + 1)):
        return None
    return lo - d, run_lo - lo + 1, lo, top


def rec_unary_perm(a, mp):
    """Single input, every output bit a copy of one input bit (or zero)."""
    res = {}
    used = a.used_inputs()
    if len(used) != 1:
        return res
    k = used[0]
    w = a.widths[k]
    off = a.offs[k]
    pairs = [(o, mp[o] - off) for o in range(a.out) if mp[o] is not None]
    if not pairs:
        return res
    out = a.out

    def put(isa, n, how):
        if isa not in res or n < res[isa][0]:
            res[isa] = (n, how)

    # same shift for every bit: shift, rotate or field extract/insert
    ds = {o - j for o, j in pairs}
    if len(ds) == 1:
        d = ds.pop()
        outs = sum(1 << o for o, _ in pairs)
        if outs == full_mask(a, k, d):
            for isa in ISAS:
                put(isa, 1 if d else 0, "shift" if d else "ext/trunc")
        elif contiguous(outs):
            put("arm", 1, "ubfx/ubfiz")
            put("x86", 1 if d <= 0 else 2,
                "bextr" if d <= 0 else "shl+and")
            put("rv", 2, "slli+srli")
    # rotate: one modular shift covering the whole word
    if out == w and len(pairs) == w:
        rs = {(o - j) % w for o, j in pairs}
        if len(rs) == 1:
            for isa in ISAS:
                put(isa, 1, "rotate")
    if out == w and len(pairs) == w:
        if all(j == w - 1 - o for o, j in pairs):
            put("arm", 1, "rbit")
            put("rv", 2 if w == 64 else 3, "rev8+brev8")
            put("x86", 3, "gf2p8affineqb+bswap (via xmm)")
        if w % 8 == 0 and all(j == (w // 8 - 1 - o // 8) * 8 + o % 8
                              for o, j in pairs):
            for isa in ISAS:
                put(isa, 1, "bswap/rev")
        if w % 8 == 0 and all(j == (o // 8) * 8 + 7 - o % 8
                              for o, j in pairs):
            put("rv", 1, "brev8")
            put("arm", 2, "rbit+rev")
            put("x86", 3, "gf2p8affineqb (via xmm)")
    # sign extension of a field (sbfx / movsx / sext.b|h|w)
    sx = sext_field(a, mp)
    if sx:
        field_lo, kbits, lo, top = sx
        aligned = lo == 0 and field_lo - off == 0 and kbits in (8, 16, 32)
        n = 1 if aligned else 2 if lo == 0 else 3
        # a sign run that stops short of the top is a narrower extension
        # zero-extended: free on x86/AArch64 when it stops at bit 31 (32-bit
        # ops clear the upper half), one more and/zext.w otherwise
        short = top != out - 1
        free32 = top == 31
        put("arm", 1 + (1 if short and not free32 else 0), "sbfx/sbfiz")
        put("x86", n + (1 if short and not free32 else 0),
            "movsx" if aligned else "shl+sar")
        put("rv", n + (1 if short else 0),
            "sext.b/h/w" if aligned else "slli+srai")
    # broadcast of one bit to a contiguous run (sign replicate, -(x>>j & 1))
    src = {j for _, j in pairs}
    outs = sum(1 << o for o, _ in pairs)
    if len(src) == 1 and len(pairs) > 1:
        top = outs == ones(out) or contiguous(outs)
        put("arm", 1 if top else 2, "sbfx")
        put("x86", 2 if top else 3, "shl+sar")
        put("rv", 2 if top else 3, "slli+srai")
    # monotone gather/scatter with constant masks
    if len(src) == len(pairs):
        js = [j for _, j in sorted(pairs)]
        if all(x < y for x, y in zip(js, js[1:])):
            srcmask = sum(1 << j for j in js)
            low = outs == ones(len(pairs))
            dense_src = contiguous(srcmask)
            n86 = 1 if (low or dense_src) else 2
            put("x86", n86, "pext" if low else "pdep" if dense_src
                else "pext+pdep")
            put("arm", 2 + n86, "SVE2 bext/bdep (via z-reg)")
        # byte permutation: whole bytes move intact
        if all(o % 8 == j % 8 for o, j in pairs) and out % 8 == 0:
            byte_ok = True
            bmap = {}
            for o, j in pairs:
                if bmap.setdefault(o // 8, j // 8) != j // 8:
                    byte_ok = False
            if byte_ok:
                put("rv", 1, "xperm8")
                put("x86", 3, "pshufb (via xmm)")
                put("arm", 3, "tbl (via v-reg)")
    # zip: interleave the two halves of one register
    if out == w and len(pairs) == w and w % 2 == 0:
        h = w // 2
        if all(j == (o // 2) + (h if o % 2 else 0) for o, j in pairs):
            put("rv", 1 if w == 32 else 4, "zip")
        if all(o == (j // 2) + (h if j % 2 else 0) for o, j in pairs):
            put("rv", 1 if w == 32 else 4, "unzip")
    return res


def rec_binary_perm(a, mp):
    """Several inputs, every output bit a copy of one input bit."""
    res = {}
    used = a.used_inputs()
    if len(used) != 2:
        return res
    pairs = [(o, a.input_of(mp[o])) for o in range(a.out) if mp[o] is not None]

    def put(isa, n, how):
        if isa not in res or n < res[isa][0]:
            res[isa] = (n, how)

    # funnel shift: out = (hi:lo) >> d, a contiguous window of the concat
    if len(pairs) == a.out and all(a.widths[k] == a.out for k in used):
        ds = {o - (a.offs[k] + j) for o, (k, j) in pairs}
        if len(ds) == 1:
            for isa in ISAS:
                put(isa, 1 if isa != "rv" else 3, "funnel (shld/extr)")
    # interleave of two equal-width inputs (Morton / zip of two registers)
    w0, w1 = (a.widths[k] for k in used)
    if w0 == w1 and a.out == 2 * w0 and len(pairs) == a.out:
        k0, k1 = used
        for lo, hi in ((k0, k1), (k1, k0)):
            if all((k, j) == ((lo, o // 2) if o % 2 == 0 else (hi, o // 2))
                   for o, (k, j) in pairs):
                put("x86", 3, "pdep+pdep+or")
                put("arm", 4, "zip1 (via v-reg)")
                put("rv", 2 if a.out == 32 else 5, "pack+zip")
    # merge: each input contributes in place under a constant mask
    if all(o == j for o, (k, j) in pairs):
        for isa in ISAS:
            put(isa, 2 if isa == "arm" else 3, "and/andn/or (bsl)")
    # pack: the low halves of two inputs side by side
    if len(pairs) == a.out and a.out % 2 == 0:
        h = a.out // 2
        lo_ok = all(o < h and (k, j) == (used[0], o) or
                    o >= h and (k, j) == (used[1], o - h) for o, (k, j) in pairs)
        if lo_ok:
            put("rv", 1, "pack")
            put("arm", 1, "bfi")
            put("x86", 2, "shl+or")
    return res


def rec_multibit(a):
    """Rows with several bits: reductions, broadcasts, clmul, GFNI blocks."""
    res = {}

    def put(isa, n, how):
        if isa not in res or n < res[isa][0]:
            res[isa] = (n, how)

    used = a.used_inputs()
    nz = [r for r in a.rows if r]
    distinct = set(nz)
    # predicate broadcast: every nonzero row tests the same set of bits
    if len(distinct) == 1 and a.op in ("or", "any"):
        n = len(nz)
        if n == 1:
            put("arm", 2, "tst+cset")
            put("x86", 2, "test+setne")
            put("rv", 2, "and+snez")
        else:
            put("arm", 2, "tst+csetm")
            put("x86", 3, "test+setne+neg")
            put("rv", 3, "and+snez+neg")
    if len(distinct) == 1 and a.op == "xor" and len(nz) == 1:
        put("x86", 3, "and+popcnt+and")
        put("arm", 4, "and+cnt (via v-reg)")
        put("rv", 3, "and+cpop+andi")
    # orc.b: each byte becomes 0x00 or 0xff by OR of its own bits
    if a.op == "or" and len(used) == 1 and a.out % 8 == 0 and \
            a.out == a.widths[used[0]]:
        off = a.offs[used[0]]
        if all(a.rows[o] == ones(8) << (off + o // 8 * 8)
               for o in range(a.out)):
            put("rv", 1, "orc.b")
    # GFNI: the same 8x8 block on every byte, XOR semiring
    if a.op in ("xor", "any") and len(used) == 1 and a.out % 8 == 0 and \
            a.out == a.widths[used[0]]:
        off = a.offs[used[0]]
        blocks = set()
        ok = True
        for o in range(a.out):
            r = a.rows[o] >> off
            byte = o // 8
            if r & ~(ones(8) << (8 * byte)):
                ok = False
                break
            blocks.add((o % 8, r >> (8 * byte)))
        if ok and len({o for o, _ in blocks}) == len(blocks):
            put("x86", 3, "gf2p8affineqb (via xmm)")
    # carry-less multiply by a constant: XOR of whole shifted copies
    if a.op in ("xor", "any") and len(used) == 1:
        k = used[0]
        dg = {d: m for (kk, d), m in a.diagonals().items() if kk == k}
        if dg and all(m == full_mask(a, k, d) for d, m in dg.items()):
            if all(d >= 0 for d in dg):
                put("x86", 3, "pclmulqdq (via xmm)")
                put("arm", 3, "pmull (via v-reg)")
                put("rv", 1, "clmul")
            elif all(d <= 0 for d in dg):
                put("rv", 2, "clmulr/clmulh")
                put("x86", 4, "pclmulqdq+shift (via xmm)")
                put("arm", 4, "pmull+shift (via v-reg)")
    return res


def _terms(a):
    """The diagonals of each input, with complementary pairs (d, d - w) that
    together make a whole rotate merged into one rotate term. Each term is
    (kind, input, d) with kind in rot, shift (whole shifted copy), part."""
    dg = a.diagonals()
    terms, used = [], set()
    for (k, d), m in sorted(dg.items()):
        w = a.widths[k]
        if a.out == w and d > 0 and (k, d - w) in dg and \
                m == full_mask(a, k, d) and \
                dg[(k, d - w)] == full_mask(a, k, d - w):
            used |= {(k, d), (k, d - w)}
            terms.append(("rot", k, d, m | dg[(k, d - w)]))
    for (k, d), m in sorted(dg.items()):
        if (k, d) in used:
            continue
        used.add((k, d))
        full = m == full_mask(a, k, d)
        terms.append(("shift" if full else "part", k, d, m))
    return terms


def _sar_rows(a, k, s):
    """Rows of ashr(input k, s), widened or truncated to the output."""
    w, off = a.widths[k], a.offs[k]
    return [1 << (off + min(o + s, w - 1)) for o in range(a.out)]


def _gf_mul(x, c, poly):
    r = 0
    while c:
        if c & 1:
            r ^= x
        x <<= 1
        if x & 0x100:
            x ^= poly
        c >>= 1
    return r


def _gf_matrices(poly):
    """8x8 block of multiplication by each constant, as 8 row masks indexed by
    output bit: rows[o] has bit j when bit o of c*x depends on bit j of x."""
    table = {}
    for c in range(1, 256):
        rows = [0] * 8
        for j in range(8):
            v = _gf_mul(1 << j, c, poly)
            for o in range(8):
                if v >> o & 1:
                    rows[o] |= 1 << j
        table[tuple(rows)] = c
    return table


GF_POLYS = {0x11B: "AES", 0x11D: "Reed-Solomon"}
_GF = {p: _gf_matrices(p) for p in GF_POLYS}

MIXCOL = ((2, 3, 1, 1), (1, 2, 3, 1), (1, 1, 2, 3), (3, 1, 1, 2))
INVMIXCOL = ((14, 11, 13, 9), (9, 14, 11, 13), (13, 9, 14, 11),
             (11, 13, 9, 14))


def gf256_coeffs(a):
    """(poly, coefficient matrix [out byte][in byte]) when the matrix is a
    GF(2^8) matrix-vector product over bytes, else None."""
    if a.op not in ("xor", "any") or a.out % 8 or a.inn % 8:
        return None
    if any(o % 8 for o in a.offs):
        return None
    nob, nib = a.out // 8, a.inn // 8
    for poly, table in _GF.items():
        coef = []
        ok = True
        for ob in range(nob):
            row = []
            for ib in range(nib):
                blk = tuple((a.rows[ob * 8 + o] >> (ib * 8)) & 0xFF
                            for o in range(8))
                if not any(blk):
                    row.append(0)
                elif blk in table:
                    row.append(table[blk])
                else:
                    ok = False
                    break
            if not ok:
                break
            coef.append(row)
        if ok and any(c > 1 for r in coef for c in r):
            return poly, coef
    return None


def rec_gf256(a):
    """AES MixColumns (and its inverse) are single instructions on AArch64
    (aesmc/aesimc) and partly on x86 (aesimc) and RISC-V (aes64im). Other
    GF(2^8) matrices, as in erasure coding, have no single instruction: GFNI
    multiplies bytes by a constant, so each distinct coefficient costs a
    multiply, a byte shuffle and an xor."""
    res = {}
    g = gf256_coeffs(a)
    if not g:
        return res
    poly, coef = g
    if poly == 0x11B and len(coef) == 4 and all(len(r) == 4 for r in coef):
        # any byte order: every row a rotation of the first, with the
        # coefficients of MixColumns or of its inverse
        rows = [tuple(r) for r in coef]
        rots = {rows[0][k:] + rows[0][:k] for k in range(4)}
        circulant = all(r in rots for r in rows) and len(set(rows)) == 4
        for want, inv in ((sorted(MIXCOL[0]), False),
                          (sorted(INVMIXCOL[0]), True)):
            if circulant and sorted(rows[0]) == want:
                res["arm"] = (3, ("aesimc" if inv else "aesmc") +
                              " (via v-reg)")
                res["x86"] = (3 if inv else 5,
                              "aesimc (via xmm)" if inv else
                              "aesdeclast+aesenc (via xmm)")
                if inv:
                    res["rv"] = (1, "aes64im (Zknd)")
    terms = {c for r in coef for c in r if c > 1}
    res.setdefault("x86", (2 + 3 * len(terms), "GFNI gf2p8mulb per coefficient"))
    return res


def network_cost(a):
    """The cheapest network, with or without one sign-replication term: an
    ashr of one input taken out first, as in x ^ (x >> 63) or the
    sign-dependent masks of branch-free code."""
    best = _network_cost(a)
    if not any(r & (r - 1) for r in a.rows):
        return best
    for k in a.used_inputs():
        w = a.widths[k]
        top = 1 << (a.offs[k] + w - 1)
        if sum(1 for r in a.rows if r & top) < 3:
            continue
        for sh in range(w):
            pat = _sar_rows(a, k, sh)
            # under OR the term must lie inside the matrix; under XOR it is
            # simply cancelled out of it
            if a.op != "xor" and any(p & ~r for p, r in zip(pat, a.rows)):
                continue
            rest = Affine.__new__(Affine)
            rest.__dict__.update(a.__dict__)
            rest.rows = [r ^ p if a.op == "xor" else r & ~p
                         for r, p in zip(a.rows, pat)]
            sub = _network_cost(rest)
            for i in ISAS:
                c, how = sub[i]
                if how == "constant":
                    extra = 1                  # the sar alone
                else:
                    # sar, then combine; AArch64 folds an asr operand
                    extra = 1 if i == "arm" else 2
                if c + extra < best[i][0]:
                    best[i] = (c + extra, f"sar + {how}")
    return best


def _network_cost(a):
    """Upper bound for anything: one term per rotate or diagonal (shift, then
    mask unless the shift already clears the rest), then combine. AArch64
    folds a shift or rotate of the second operand into eor/orr, and ubfx/ubfiz
    do a shift and a contiguous mask in one."""
    terms = _terms(a)
    if not terms:
        return {isa: (0, "constant") for isa in ISAS}
    n = len(terms)
    x86 = rv = n - 1
    arm = n - 1
    for i, (kind, _, d, m) in enumerate(terms):
        moved = kind == "rot" or d != 0
        if kind in ("rot", "shift"):
            x86 += 1 if moved else 0
            rv += 1 if moved else 0
            arm += 0 if i else (1 if moved else 0)
        else:
            x86 += 1 + (1 if d else 0)
            rv += 1 + (1 if d else 0)
            arm += 1 if (i or contiguous(m) or not d) else 2
    tail = 1 if a.b else 0
    how = f"{n}-term {'xor' if a.op == 'xor' else 'or'} network"
    return {"x86": (x86 + tail, how), "arm": (arm + tail, how),
            "rv": (rv + tail, how)}


# XOR of three rotates/shifts of one word that some ISA does in one
# instruction: SHA-2 sigma/Sigma (RISC-V Zknh) and SM3 P0/P1 (Zksh).
# Each entry: (width, ((kind, amount), ...)) with kind "ror", "shr", "id".
_CRYPTO = {
    "sha256sum0": (32, (("ror", 2), ("ror", 13), ("ror", 22))),
    "sha256sum1": (32, (("ror", 6), ("ror", 11), ("ror", 25))),
    "sha256sig0": (32, (("ror", 7), ("ror", 18), ("shr", 3))),
    "sha256sig1": (32, (("ror", 17), ("ror", 19), ("shr", 10))),
    "sha512sum0": (64, (("ror", 28), ("ror", 34), ("ror", 39))),
    "sha512sum1": (64, (("ror", 14), ("ror", 18), ("ror", 41))),
    "sha512sig0": (64, (("ror", 1), ("ror", 8), ("shr", 7))),
    "sha512sig1": (64, (("ror", 19), ("ror", 61), ("shr", 6))),
    "sm3p0": (32, (("id", 0), ("ror", 23), ("ror", 15))),
    "sm3p1": (32, (("id", 0), ("ror", 17), ("ror", 9))),
}


def _crypto_rows(w, parts):
    rows = [0] * w
    for kind, s in parts:
        for o in range(w):
            if kind == "id":
                j = o
            elif kind == "ror":
                j = (o + s) % w
            else:
                j = o + s
                if j >= w:
                    continue
            rows[o] ^= 1 << j
    return rows


def rec_crypto(a):
    res = {}
    used = a.used_inputs()
    if len(used) != 1 or a.op not in ("xor", "any"):
        return res
    k = used[0]
    w = a.widths[k]
    if a.out != w:
        return res
    rows = [r >> a.offs[k] for r in a.rows]
    for name, (cw, parts) in _CRYPTO.items():
        if cw == w and rows == _crypto_rows(w, parts):
            res["rv"] = (1, name + " (Zknh/Zksh)")
    return res


def _block_rec(a, k, rows):
    """The single-input record for input k's columns of the matrix."""
    out, w = a.out, a.widths[k]
    m = 0
    for o, r in enumerate(rows):
        m |= ((r >> a.offs[k]) & ones(w)) << ((out - 1 - o) * w)
    return {"width": out, "in_width": w, "inputs": [{"w": w}],
            "op": a.op, "matrix": format(m, "x"), "mask": "0"}


def block_cost(a):
    """A matrix over several inputs is at most the sum of its per-input
    blocks plus one combine each; when every block is the same operation on
    equal-width inputs, combine first and apply it once."""
    used = a.used_inputs()
    if len(used) < 2:
        return None
    blocks = []
    for k in used:
        sel = ones(a.widths[k]) << a.offs[k]
        rows = [r & sel for r in a.rows]
        blocks.append((k, tuple((r >> a.offs[k]) for r in rows)))
    tail = 1 if a.b else 0
    n = len(blocks)
    best = {}
    same = len({b for _, b in blocks}) == 1 and \
        len({a.widths[k] for k, _ in blocks}) == 1
    if same:
        k = blocks[0][0]
        sub = isa_costs(_block_rec(a, k, [r for r in a.rows]))
        for i, (c, how) in sub.items():
            best[i] = (c + n - 1 + tail, f"combine, then {how}")
    total = {i: 0 for i in ISAS}
    hows = []
    for k, _ in blocks:
        sel = ones(a.widths[k]) << a.offs[k]
        sub = isa_costs(_block_rec(a, k, [r & sel for r in a.rows]))
        for i in ISAS:
            total[i] += sub[i][0]
        hows.append(sub["x86"][1])
    for i in ISAS:
        c = total[i] + n - 1 + tail
        if i not in best or c < best[i][0]:
            best[i] = (c, "per input: " + " | ".join(hows))
    return best


def isa_costs(rec):
    """{isa: (instruction count, how)}: the cheapest of the recognized forms,
    the per-input decomposition and the network bound."""
    a = Affine(rec)
    best = network_cost(a)
    mp = a.single()
    found = {}
    if mp is not None:
        for f in (rec_unary_perm, rec_binary_perm):
            for i, v in f(a, mp).items():
                if i not in found or v[0] < found[i][0]:
                    found[i] = v
    for f in (rec_multibit, rec_crypto, rec_gf256):
        for i, v in f(a).items():
            if i not in found or v[0] < found[i][0]:
                found[i] = v
    for i, (n, how) in found.items():
        # an inverted predicate is the opposite condition code, not an xor
        free = any(t in how for t in ("csetm", "cset", "setne", "snez", "neg"))
        n += 1 if a.b and not free else 0
        if n < best[i][0]:
            best[i] = (n, how)
    bc = block_cost(a)
    if bc:
        for i, v in bc.items():
            if v[0] < best[i][0]:
                best[i] = v
    return best


def classify(rec):
    """A coarse operation class, for tables."""
    a = Affine(rec)
    used = a.used_inputs()
    mp = a.single()
    nz = [r for r in a.rows if r]
    if not nz:
        return "constant"
    if mp is not None:
        srcs = [j for j in mp if j is not None]
        if len(set(srcs)) < len(srcs):
            sx = sext_field(a, mp)
            return "sign-extend" if sx and sx[1] > 1 else "broadcast"
        ds = {o - j for o, j in enumerate(mp) if j is not None}
        if len(used) > 1:
            if all(o == a.input_of(j)[1] for o, j in enumerate(mp)
                   if j is not None):
                return "merge"
            return "interleave/pack"
        if len(ds) == 1:
            return "shift/extract"
        w = a.widths[used[0]]
        if len({(o - j) % w for o, j in enumerate(mp) if j is not None}) == 1:
            return "rotate"
        js = [j for j in mp if j is not None]
        if all(x < y for x, y in zip(js, js[1:])):
            return "gather/scatter"
        return "permutation"
    if len(set(nz)) == 1:
        return "predicate" if a.op != "xor" else "parity"
    g = gf256_coeffs(a)
    if g:
        return f"gf256-linear ({GF_POLYS[g[0]]})"
    if a.op == "xor":
        return "xor-linear" + ("-binary" if len(used) > 1 else "")
    return "or-linear" + ("-binary" if len(used) > 1 else "")


def _self_test():
    def mk(out, rows, inputs=None, op="any", b=0):
        inn = sum(inputs) if inputs else out
        m = 0
        for o, r in enumerate(rows):
            m |= r << ((out - 1 - o) * inn)
        return {"width": out, "in_width": inn,
                "inputs": [{"w": w} for w in (inputs or [out])],
                "op": op, "matrix": format(m, "x"), "mask": format(b, "x")}

    def check(name, rec, isa, want):
        got = isa_costs(rec)[isa]
        status = "ok" if got[0] == want else "FAIL"
        print(f"{status:4} {name:28} {isa}: {got} (want {want})")
        return status == "ok"

    W = 32
    ok = True
    rot = mk(W, [1 << ((o - 7) % W) for o in range(W)])
    ok &= check("rotl 7", rot, "x86", 1)
    rev = mk(W, [1 << (W - 1 - o) for o in range(W)])
    ok &= check("bitreverse", rev, "arm", 1)
    bsw = mk(W, [1 << ((3 - o // 8) * 8 + o % 8) for o in range(W)])
    ok &= check("bswap", bsw, "rv", 1)
    pm = [o * 2 + 1 for o in range(8)]
    pe = mk(W, [1 << pm[o] if o < 8 else 0 for o in range(W)])
    ok &= check("pext odd bits", pe, "x86", 1)
    cl = mk(W, [(1 << o) | ((1 << (o - 3)) if o >= 3 else 0)
                for o in range(W)], op="xor")
    ok &= check("x ^ x<<3 (clmul 9)", cl, "rv", 1)
    bc = mk(W, [1 << 7] * W)
    ok &= check("broadcast bit 7", bc, "arm", 1)
    zm = mk(W, [0xFF] * W, op="or", b=ones(W))
    ok &= check("byte == 0 mask", zm, "arm", 2)
    mo = mk(32, [1 << (o // 2) if o % 2 == 0 else 1 << (16 + o // 2)
                 for o in range(32)], inputs=[16, 16])
    ok &= check("morton 16+16", mo, "x86", 3)
    bl = mk(64, [(1 << ((o + 32) % 64)) | (1 << (64 + (o + 32) % 64))
                 for o in range(64)], inputs=[64, 64], op="xor")
    ok &= check("rotr(a ^ b, 32)", bl, "x86", 2)
    s0 = mk(32, _crypto_rows(32, _CRYPTO["sha256sig0"][1]), op="xor")
    ok &= check("sha256 sigma0", s0, "rv", 1)
    ok &= check("sha256 sigma0", s0, "arm", 3)
    sx = mk(64, [1 << (o if o < 32 else 31) for o in range(64)])
    ok &= check("sext i32 -> i64", sx, "x86", 1)
    ok &= check("sext i32 -> i64", sx, "rv", 1)
    zs = mk(64, [1 << (o if o < 8 else 7) if o < 32 else 0
                 for o in range(64)], inputs=[8])
    ok &= check("zext(sext i8 -> i32)", zs, "x86", 1)
    xs = mk(64, [(1 << o) | (1 << 63) if o < 63 else 0 for o in range(64)],
            op="xor")
    ok &= check("x ^ (x >>s 63)", xs, "arm", 1)
    ok &= check("x ^ (x >>s 63)", xs, "x86", 2)
    # AES MixColumns over one i32 column, byte i = bits 8i..8i+7
    mc_rows = [0] * 32
    for ob in range(4):
        for ib in range(4):
            blk = _gf_matrices(0x11B)
            inv = {v: k for k, v in blk.items()}[MIXCOL[ob][ib]]
            for o in range(8):
                mc_rows[ob * 8 + o] |= inv[o] << (ib * 8)
    mc = mk(32, mc_rows, op="xor")
    ok &= check("AES MixColumns", mc, "arm", 3)
    print("gf256:", classify(mc))
    print("classes:", classify(sx), classify(rot), classify(bc), classify(zm), classify(mo),
          classify(cl))
    return ok


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(0 if _self_test() else 1)
    print(__doc__)
