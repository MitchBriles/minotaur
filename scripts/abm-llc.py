#!/usr/bin/env python3
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
"""
What do today's compilers emit for each census region?

abm-isa.py estimates what an ideal use of each ISA would cost; this measures
what LLVM actually produces. Each distinct region's @src is compiled with llc
for the same three targets and the instructions are counted (not ret, labels
or directives; constant materialization does count, unlike in abm-isa.py).
Register-to-register moves are not counted either. Results are memoized by a
hash of the region, in JSONL.

  abm-llc.py -j 8 --memo llc.jsonl census.jsonl
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import subprocess
import sys

LLC = os.environ.get(
    "LLC", os.path.expanduser("~/research/llvm-project/build/bin/llc"))

TARGETS = {
    "x86": ["-mtriple=x86_64-unknown-linux-gnu", "-mcpu=x86-64-v4",
            "-mattr=+bmi,+bmi2,+gfni,+pclmul,+avx512vbmi,+avx512bitalg"],
    "arm": ["-mtriple=aarch64-linux-gnu",
            "-mattr=+v9a,+sve2,+sve2-bitperm,+sha3,+crc,+aes"],
    "rv": ["-mtriple=riscv64-linux-gnu",
           "-mattr=+m,+a,+f,+d,+c,+v,+zba,+zbb,+zbs,+zbkb,+zbkx,+zbc,"
           "+zknh,+zicond"],
}

INSN = re.compile(r"^\s+([a-z][\w.]*)")
# register-to-register copies: forced by x86's two-address form and usually
# eliminated at rename, so they are not counted as work
REGMOVE = re.compile(r"^\s+(mov[lqwb]?\s+%\w+,\s*%\w+|mov\s+[wx]\d+,\s*[wx]\d+"
                     r"|mv\s+\w+,\s*\w+)\s*$")


def cut_key(cut):
    return hashlib.sha1(cut.encode()).hexdigest()


def count(asm):
    n = 0
    for line in asm.splitlines():
        m = INSN.match(line)
        if not m or line.lstrip().startswith((".", "#", "//")):
            continue
        if m.group(1) in ("ret", "retq") or REGMOVE.match(line):
            continue
        n += 1
    return n


def compile_all(cut, mem_mb):
    res = {}
    for name, flags in TARGETS.items():
        cmd = ["prlimit", f"--as={mem_mb << 20}", "timeout", "-k", "5", "60",
               LLC, "-O2", *flags, "-o", "-", "-"]
        p = subprocess.run(cmd, input=cut, capture_output=True, text=True)
        res[name] = count(p.stdout) if p.returncode == 0 else None
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--memo", required=True)
    ap.add_argument("-j", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--mem", type=int, default=2048, help="MB per llc")
    a = ap.parse_args()

    memo = set()
    if os.path.exists(a.memo):
        with open(a.memo) as f:
            for line in f:
                try:
                    memo.add(json.loads(line)["key"])
                except (ValueError, KeyError):
                    pass
    todo = {}
    for path in a.logs:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "census" or "cut" not in r:
                    continue
                k = cut_key(r["cut"])
                if k not in memo and k not in todo:
                    todo[k] = r["cut"]
    print(f"[abm-llc] {len(memo)} memoized, {len(todo)} to compile",
          file=sys.stderr)
    with open(a.memo, "a") as out, cf.ThreadPoolExecutor(a.j) as ex:
        futs = {ex.submit(compile_all, c, a.mem): k for k, c in todo.items()}
        for n, fut in enumerate(cf.as_completed(futs), 1):
            out.write(json.dumps({"key": futs[fut], "llc": fut.result()}) +
                      "\n")
            out.flush()
            if n % 500 == 0:
                print(f"[abm-llc] {n}/{len(todo)}", file=sys.stderr)
    print("[abm-llc] done", file=sys.stderr)


if __name__ == "__main__":
    main()
