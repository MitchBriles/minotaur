#!/usr/bin/env python3

# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.

"""Rank the ABM matrices Minotaur synthesized, by how often they occur.

Reads the JSONL logs written by `opt -minotaur-abm-log=...` (see abm-collect)
and groups them on (width, matrix), which is the ranking ABM.md asks for:
"rank matrices based on their commonality". Masks are reported per matrix
rather than folded into the key.

Matrix rendering matches ctom.py: row 0 on top is the most significant bit of
the result, column N-1 on the left is the most significant bit of the input,
i.e. out[N-1-i] = XOR over j of M[i*N + j] & x[j].
"""

import argparse
import json
import sys
from collections import Counter, OrderedDict

HIGHLIGHT = "\033[0;30;47m1\033[0m"


def rows(matrix, n):
    """The n rows of the matrix, most-significant output bit first."""
    bits = f"{matrix:0{n * n}b}"
    return [bits[i:i + n] for i in range(n * n - n, -1, -n)]


def render(matrix, n, color=False, indent="    "):
    out = []
    for row in rows(matrix, n):
        row = " ".join(row)
        if color:
            row = row.replace("1", HIGHLIGHT)
        out.append(indent + row)
    return "\n".join(out)


def collapse(idxs):
    """[8,9,10,11,15] -> '15,11..8' (descending, like bit numbering)."""
    runs, start = [], None
    prev = None
    for j in idxs:
        if start is None:
            start = prev = j
        elif j == prev + 1:
            prev = j
        else:
            runs.append((start, prev))
            start = prev = j
    if start is not None:
        runs.append((start, prev))
    parts = [f"{hi}..{lo}" if hi != lo else f"{lo}"
             for lo, hi in reversed(runs)]
    return parts


def render_summary(matrix, n, op="or", indent="    "):
    """Per output bit, with shift-like runs collapsed. Readable where a grid
    is not: a 64-bit shift is one line rather than 61."""
    join = " ^ " if op == "xor" else " | "
    entries, zero = [], 0
    for i, row in enumerate(rows(matrix, n)):
        # row i drives out[n-1-i]; row chars are x[n-1] .. x[0] left to right
        js = sorted(n - 1 - k for k, c in enumerate(row) if c == "1")
        if js:
            entries.append((n - 1 - i, js))
        else:
            zero += 1

    out, k = [], 0
    while k < len(entries):
        ob, js = entries[k]
        # merge a run of consecutive output bits each fed by one input bit at
        # a constant offset -- i.e. a shift, rotate or plain copy
        j = k
        if len(js) == 1:
            delta = ob - js[0]
            while (j + 1 < len(entries) and len(entries[j + 1][1]) == 1
                   and entries[j + 1][0] == entries[j][0] - 1
                   and entries[j + 1][0] - entries[j + 1][1][0] == delta):
                j += 1
        if j > k:
            lo_ob = entries[j][0]
            out.append(f"{indent}out[{ob}..{lo_ob}] = "
                       f"x[{ob - delta}..{lo_ob - delta}]")
        else:
            terms = join.join(f"x[{p}]" for p in collapse(js))
            out.append(f"{indent}out[{ob}] = {terms}")
        k = j + 1

    if zero:
        out.append(f"{indent}({zero} of {n} output bits are 0)")
    return "\n".join(out)


def render_llvm(matrix, n, indent="    "):
    rs = rows(matrix, n)
    out = [f"{indent}@BIG_vec = constant <{n} x i{n}> <"]
    for k, row in enumerate(rs):
        comma = "," if k < len(rs) - 1 else ""
        out.append(f"{indent}  i{n} {int(row, 2)}{comma}")
    out.append(indent + ">")
    return "\n".join(out)


def load(paths, kinds):
    records, bad = [], 0
    for path in paths:
        if path == "-":
            stream = sys.stdin
        else:
            try:
                stream = open(path)
            except OSError as e:
                print(f"{path}: {e.strerror}", file=sys.stderr)
                continue
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    r["matrix"] = int(r["matrix"], 16)
                    r["width"] = int(r["width"])
                except (ValueError, TypeError, KeyError):
                    bad += 1
                    continue
                if r.get("kind") in kinds:
                    records.append(r)
        finally:
            if stream is not sys.stdin:
                stream.close()
    return records, bad


def project_of(source):
    """Which program a record came from.

    Corpus paths look like bench/<project>/original/<file>.ll; anything else
    falls back to the containing directory so ad hoc runs still group.
    """
    parts = (source or "?").split("/")
    if len(parts) >= 3 and parts[0] == "bench":
        return parts[1]
    return parts[-2] if len(parts) >= 2 else "?"


def group(records, order="count"):
    groups = OrderedDict()
    for r in records:
        # the two semirings are different instructions, so never merge them
        key = (r.get("op", "or"), r["width"], r["matrix"])
        g = groups.setdefault(key, {"count": 0, "freq": 0.0, "depth": 0,
                                    "masks": Counter(), "sites": Counter(),
                                    "projects": Counter(), "examples": []})
        g["projects"][project_of(r.get("source"))] += 1
        g["count"] += 1
        g["freq"] += float(r.get("rel_freq", 1.0) or 0.0)
        g["depth"] = max(g["depth"], int(r.get("loop_depth", 0) or 0))
        g["masks"][r.get("mask")] += 1
        g["sites"][(r.get("function", "?"), r.get("source", "?"))] += 1
        if r.get("src") and len(g["examples"]) < 8:
            g["examples"].append(r["src"])
    rank = {"count": lambda g: -g["count"],
            "freq": lambda g: -g["freq"],
            "depth": lambda g: (-g["depth"], -g["count"]),
            # commonality across independent programs is the criterion ABM.md
            # sets, and it is what a new opcode has to justify itself against
            "projects": lambda g: (-len(g["projects"]), -g["count"])}[order]
    # ties broken deterministically
    return sorted(groups.items(),
                  key=lambda kv: (rank(kv[1]), kv[0][1], kv[0][2]))


def fanin(matrix, n):
    """Per output bit, high to low, the input bits feeding it.

    out[n-1-i] = OP over j of M[i*n + j] & x[j], so row i drives out[n-1-i].
    """
    return [(n - 1 - i,
             [j for j in range(n) if (matrix >> (i * n + j)) & 1])
            for i in range(n)]


def support(matrix, n):
    """Which input bits the matrix reads and which output bits it drives.

    The input support is what decides whether an operation is a small table: a
    matrix reading k bits is a function of 2^k inputs, so at k=8 it is a
    256-entry lookup regardless of how wide the value is.
    """
    ins, outs = set(), set()
    for i in range(n):
        for j in range(n):
            if (matrix >> (i * n + j)) & 1:
                ins.add(j)
                outs.add(n - 1 - i)
    return ins, outs


def collapse_class(matrix, n, op):
    """What ABM(x, M, x) reduces to, for a runtime mask that is the input.

    For XOR this is exact: ABMX(x, M, x) = (M ^ I) x. A record whose collapse
    is something an ISA already has replaced a trivial cut, however rich the
    matrix looks.
    """
    if op != "xor":
        return None
    ident = 0
    for k in range(n):
        ident |= 1 << ((n - 1 - k) * n + k)
    return classify(matrix ^ ident, n)


# Matrices in these classes are already covered by one ordinary instruction, so
# they are noise for the research query even though they are valid rewrites.
CHEAP = {"zero", "identity", "shift/extract", "permutation", "negation"}


def classify(matrix, n):
    """Name the family a matrix belongs to; see CHEAP for the cheap ones."""
    nz = [(o, js) for o, js in fanin(matrix, n) if js]
    if not nz:
        return "zero"
    if all(len(js) == 1 for _, js in nz):
        deltas = {o - js[0] for o, js in nz}
        if deltas == {0} and len(nz) == n:
            # ABM(x, I, b) is just x ^ b
            return "identity"
        # a single constant offset is a shift or a bit extract
        return "shift/extract" if len(deltas) == 1 else "permutation"
    # out[o] = OR of every bit below o is the borrow chain of -x; with a
    # runtime mask of x it is two's complement negation
    if all(js == list(range(0, o)) for o, js in nz if o > 0):
        return "negation"
    return "multi-bit"


def combines_bits(matrix, n):
    """True if some output bit is fed by more than one input bit -- i.e. the
    matrix does more than move bits around."""
    return any(sum((matrix >> (i * n + j)) & 1 for j in range(n)) > 1
               for i in range(n))


def load_mincost():
    """abm-mincost.py lives beside this script; its name has a hyphen so it has
    to come in through importlib rather than a plain import."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "abm-mincost.py")
    spec = importlib.util.spec_from_file_location("abm_mincost", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def report_tables(ranked, args):
    """Operations over a small input domain.

    Any operation a program performs often is of interest, not only the ones
    that look like bit twiddling. A matrix reading few input bits is a lookup
    table, which is exactly what dedicated hardware is good at, so these are
    reported on their own terms rather than filtered out for being "just" a
    table.
    """
    mc = load_mincost()
    out = []
    for (op, w, m), g in ranked:
        if len(g["projects"]) < args.min_projects:
            continue
        ins, outs = support(m, w)
        if not ins or len(ins) > args.max_input_bits:
            continue
        # a pure extract or permutation needs no table
        if classify(m, w) in CHEAP:
            continue
        out.append(((op, w, m), g, ins, outs))

    out.sort(key=lambda e: (-len(e[1]["projects"]), -e[1]["sites"]
                            if "sites" in e[1] else -e[1]["count"], len(e[2])))
    if args.top:
        out = out[:args.top]

    print(f"# {len(out)} small-domain operation(s), at most "
          f"{args.max_input_bits} input bits, ranked by how many projects "
          f"perform them")

    for i, ((op, w, m), g, ins, outs) in enumerate(out):
        fn = "@ABMX" if op == "xor" else "@ABM"
        mask_hex = g["masks"].most_common(1)[0][0]
        print(f"\n#{i + 1}  {fn}  i{w}  {len(ins)} -> {len(outs)} bits "
              f"({2 ** len(ins)} entry table)  projects={len(g['projects'])}  "
              f"sites={g['count']}")
        body = render_summary(m, w, op).splitlines()
        if len(body) > args.summary_lines:
            keep = args.summary_lines - 1
            body = body[:keep] + [f"    ... ({len(body) - keep} more rows)"]
        print("\n".join(body))
        print(f"    reads bits: {sorted(ins)}")
        print(f"    mask:   {fmt_mask(mask_hex, w)}")
        if mask_hex is None:
            cc = collapse_class(m, w, op)
            if cc:
                note = ("reduces to a " + cc if cc in CHEAP
                        else "stays " + cc)
                print(f"    with mask = x: {note}")
        if mc is not None:
            steps, n_instr = mc.lower_matrix(
                m, int(mask_hex, 16) if mask_hex else 0, w, op)
            print(f"    implementation: {n_instr} instructions")
        print(f"    projects: {', '.join(sorted(g['projects']))}")
        sites = ", ".join(f for (f, _), _ in g["sites"].most_common(3))
        print(f"    sites:    {sites[:100]}")
    return 0


def report_candidates(ranked, args):
    """The headline question: which operations are worth an instruction?

    Keeps only matrices that no one or two ordinary instructions can express,
    and orders them by how many independent programs perform them.
    """
    mc = load_mincost()
    if mc is None:
        print("abm-mincost.py not importable; --candidates needs it",
              file=sys.stderr)
        return 1

    out = []
    for (op, w, m), g in ranked:
        if len(g["projects"]) < args.min_projects:
            continue
        if classify(m, w) in CHEAP:
            continue
        if w > args.width_max:
            continue
        # judge on the inputs that can actually reach the site
        kz = ko = 0
        mask_hex = g["masks"].most_common(1)[0][0]
        mask = int(mask_hex, 16) if mask_hex else 0
        xs = mc.samples(w, known_zero=kz, known_one=ko)
        target = tuple(mc.abm_apply(v, m, mask, w, op == "xor") for v in xs)
        found = mc.search(target, xs, w, mask, args.max_len)
        if found is not None and found.length < 3:
            continue

        # A runtime mask can be the input itself, and then the mask cancels
        # part of the matrix: ABMX(x, I ^ P, x) is just P(x). Such a record
        # looks like a rich operation but the cut it replaced is trivial, so
        # judge it by what it collapses to.
        if g["masks"].most_common(1)[0][0] is None:
            if op == "xor":
                # for XOR the collapse is exact: ABMX(x, M, x) = (M ^ I) x
                ident = 0
                for k in range(w):
                    ident |= 1 << ((w - 1 - k) * w + k)
                if classify(m ^ ident, w) in CHEAP:
                    continue
            else:
                collapsed = tuple(v ^ t for v, t in zip(xs, target))
                if collapsed == tuple(xs):
                    continue
                c_found = mc.search(collapsed, xs, w, 0, args.max_len)
                if c_found is not None and c_found.length < 3:
                    continue
        if found is not None:
            steps = [t.strip() for t in found.text.split("\n")[1:] if t.strip()]
            n_instr, exact = found.length, True
        else:
            # the bounded search gives up past depth 3; the structural
            # lowering always produces something, as an upper bound
            steps, n_instr = mc.lower_matrix(m, mask, w, op)
            exact = False
        if n_instr > args.max_instructions:
            continue
        out.append(((op, w, m), g, mask, steps, n_instr, exact))

    out.sort(key=lambda e: (-len(e[1]["projects"]), -e[4], -e[1]["count"]))
    if args.top:
        out = out[:args.top]

    print(f"# {len(out)} candidate operation(s): 3 or more instructions "
          f"without ABM, ranked by how many projects perform them")

    for i, ((op, w, m), g, mask, steps, n_instr, exact) in enumerate(out):
        fn = "@ABMX" if op == "xor" else "@ABM"
        how = "minimum" if exact else "at most"
        print(f"\n#{i + 1}  {fn}  i{w}  projects={len(g['projects'])}  "
              f"sites={g['count']}  instructions={how} {n_instr}")
        body = (render_summary(m, w, op)
                if w > args.grid_max_width or args.summary
                else render(m, w, args.color))
        # a wide matrix can summarise to one line per output bit, which drowns
        # the report; the full form is a rank away with --summary
        lines = body.splitlines()
        if len(lines) > args.summary_lines:
            keep = args.summary_lines - 1
            lines = lines[:keep] + [f"    ... ({len(lines) - keep} more rows)"]
        print("\n".join(lines))
        mhex = f"0x{m:x}"
        if len(mhex) > 26:
            mhex = f"{mhex[:12]}...{mhex[-8:]} ({m.bit_count()} bits set)"
        print(f"    matrix: {mhex}")
        print(f"    mask:   {fmt_mask(g['masks'].most_common(1)[0][0], w)}")
        print("    implementation:" if not exact
              else "    minimal implementation:")
        for step in steps:
            print(f"      {step}")
        print(f"    projects: {', '.join(sorted(g['projects']))}")
        sites = ", ".join(f"{f}" for (f, _), _ in g["sites"].most_common(3))
        print(f"    sites:    {sites}")
    return 0


def fmt_mask(m, n):
    if m is None:
        return "<runtime value>"
    return f"0x{int(m, 16):0{max(1, (n + 3) // 4)}x}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="*", default=["-"],
                    help="JSONL logs from -minotaur-abm-log (default: stdin)")
    ap.add_argument("--all", action="store_true",
                    help="include kind=candidate and kind=extracted records, "
                         "not just accepted")
    ap.add_argument("--extracted", action="store_true",
                    help="only matrices recovered by basis-vector extraction; "
                         "these carry an evidence field and may not have been "
                         "SMT verified")
    ap.add_argument("--top", type=int, metavar="N", help="show only the top N")
    ap.add_argument("--color", action="store_true", help="highlight set bits")
    ap.add_argument("--grid", action="store_true",
                    help="always draw the full bit grid, however wide")
    ap.add_argument("--summary", action="store_true",
                    help="always summarize per output bit instead of a grid")
    ap.add_argument("--grid-max-width", type=int, default=16, metavar="N",
                    help="draw a grid up to this width, summarize above it "
                         "(default 16)")
    ap.add_argument("--llvm", action="store_true",
                    help="also print the matrix as an LLVM vector constant")
    ap.add_argument("--src-lines", type=int, default=6, metavar="N",
                    help="lines of replaced IR to show, 0 to hide (default 6)")
    ap.add_argument("--json", action="store_true",
                    help="emit the ranking as JSON instead of a report")
    ap.add_argument("--interesting", action="store_true",
                    help="keep only matrices that need more than one ordinary "
                         "instruction: drops identities, shifts, extracts, "
                         "permutations and negations, which existing ISAs "
                         "already cover")
    ap.add_argument("--multi-bit", action="store_true",
                    help="keep only matrices that combine more than one input "
                         "bit into some output bit; drops the shifts, byte "
                         "swaps and bit extracts that existing ISAs cover")
    ap.add_argument("--by", choices=("count", "freq", "depth", "projects"),
                    default="count",
                    help="rank by number of sites (default), by summed "
                         "execution frequency, by deepest loop nesting, or by "
                         "how many distinct projects a matrix appears in")
    ap.add_argument("--tables", action="store_true",
                    help="report operations over a small input domain: a "
                         "matrix reading k input bits is a 2^k entry lookup, "
                         "which is a shape hardware implements well. Ranked "
                         "by how many projects perform them")
    ap.add_argument("--max-input-bits", type=int, default=12, metavar="N",
                    help="with --tables, the largest input support to report "
                         "(default 12, a 4096 entry table)")
    ap.add_argument("--candidates", action="store_true",
                    help="report the top candidate operations: only matrices "
                         "needing 3 or more ordinary instructions, ranked by "
                         "how many independent projects use them, each shown "
                         "with a minimal implementation")
    ap.add_argument("--min-projects", type=int, default=2, metavar="N",
                    help="with --candidates, require at least N distinct "
                         "projects (default 2). Recurrence across independent "
                         "programs is the criterion, and it also excludes the "
                         "one-off matrices the synthesizer builds by "
                         "exploiting inputs a site cannot reach")
    ap.add_argument("--max-instructions", type=int, default=32, metavar="N",
                    help="with --candidates, drop matrices whose "
                         "implementation exceeds N instructions (default 32); "
                         "past that the matrix is dense noise rather than one "
                         "instruction's worth of structure")
    ap.add_argument("--summary-lines", type=int, default=14, metavar="N",
                    help="with --candidates, cap the matrix summary at N rows "
                         "(default 14)")
    ap.add_argument("--max-len", type=int, default=3, metavar="N",
                    help="with --candidates, longest sequence to search for "
                         "(default 3)")
    ap.add_argument("--width-max", type=int, default=64, metavar="N",
                    help="with --candidates, skip matrices wider than this "
                         "(default 64, which covers i64 bitboard code)")
    ap.add_argument("--op", choices=("or", "xor"),
                    help="keep only @ABM (or) or only @ABMX (xor) records")
    args = ap.parse_args()

    if args.extracted:
        kinds = {"extracted"}
    elif args.all:
        kinds = {"accepted", "candidate", "extracted"}
    else:
        kinds = {"accepted"}
    records, bad = load(args.logs, kinds)
    if bad:
        print(f"warning: skipped {bad} unparseable line(s)", file=sys.stderr)
    if not records:
        print("no matching ABM records", file=sys.stderr)
        return 1

    if args.op:
        records = [r for r in records if r.get("op", "or") == args.op]
        if not records:
            print(f"no @ABM{'X' if args.op == 'xor' else ''} records",
                  file=sys.stderr)
            return 1

    if args.multi_bit:
        records = [r for r in records if combines_bits(r["matrix"], r["width"])]
        if not records:
            print("no matrix combines more than one input bit", file=sys.stderr)
            return 1

    if args.interesting:
        records = [r for r in records
                   if classify(r["matrix"], r["width"]) not in CHEAP]
        if not records:
            print("no matrix needs more than one ordinary instruction",
                  file=sys.stderr)
            return 1

    ranked = group(records,
                   "projects" if (args.candidates or args.tables) else args.by)

    if args.tables:
        return report_tables(ranked, args)

    if args.candidates:
        return report_candidates(ranked, args)

    shown = ranked[:args.top] if args.top else ranked

    if args.json:
        json.dump([{
            "rank": i + 1,
            "op": op,
            "class": classify(m, w),
            "count": g["count"],
            "freq": round(g["freq"], 6),
            "max_loop_depth": g["depth"],
            "width": w,
            "matrix": f"0x{m:x}",
            "masks": {("null" if k is None else k): v
                      for k, v in g["masks"].most_common()},
            "sites": [{"function": f, "source": s, "count": c}
                      for (f, s), c in g["sites"].most_common()],
        } for i, ((op, w, m), g) in enumerate(shown)], sys.stdout, indent=2)
        print()
        return 0

    kindstr = "+".join(sorted(kinds))
    plural = "matrix" if len(ranked) == 1 else "matrices"
    print(f"# {len(records)} {kindstr} records, "
          f"{len(ranked)} distinct {plural}")
    if args.top and len(ranked) > args.top:
        print(f"# showing top {args.top}")

    for i, ((op, w, m), g) in enumerate(shown):
        print()
        mhex = f"0x{m:x}"
        if len(mhex) > 26 and not args.grid:
            mhex = f"{mhex[:12]}...{mhex[-8:]} ({m.bit_count()} bits set)"
        fn = "@ABMX" if op == "xor" else "@ABM"
        print(f"#{i + 1}  {fn}  count={g['count']}  freq={g['freq']:g}  "
              f"depth={g['depth']}  width={w}  class={classify(m, w)}  "
              f"matrix={mhex}")
        as_grid = args.grid or (not args.summary and w <= args.grid_max_width)
        print(render(m, w, args.color) if as_grid
              else render_summary(m, w, op))
        if args.llvm:
            print()
            print(render_llvm(m, w))
        masks = ", ".join(f"{fmt_mask(k, w)} x{v}"
                          for k, v in g["masks"].most_common())
        print(f"    mask: {masks}")
        sites = ", ".join(f"{f} @ {s}" for (f, s), _ in g["sites"].most_common(4))
        more = len(g["sites"]) - 4
        print(f"    from: {sites}" + (f", +{more} more" if more > 0 else ""))
        if args.src_lines and g["examples"]:
            print("    replaced:")
            lines = g["examples"][0].rstrip().splitlines()
            for line in lines[:args.src_lines]:
                print("      " + line)
            if len(lines) > args.src_lines:
                print(f"      ... ({len(lines) - args.src_lines} more lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
