# What bitwise operations do programs actually perform?

Corpus: 16 projects, 417 optimized IR files from `llvm-opt-benchmark`, spanning compression
(libdeflate, lz4, zstd, brotli, zfp), crypto (libsodium, wolfssl), media (libpng, flac), regex
(re2, oniguruma), allocators (mimalloc), numerics (softposit-rs) and language runtimes (lua,
luajit, quickjs).

Run: `abm-collect -t 240 -j 12 -a -x '-minotaur-function-to=45'`, about 2h20m.
24825 records (2373 accepted, 16605 candidate, 5847 extracted), **1841 distinct matrices**.
Semirings: 15236 OR, 9589 XOR. Widths: i8 4232, i16 1044, i32 12260, i64 7052.

## The answer

| class | matrices | in 2+ projects |
|---|---|---|
| shift / extract | 1291 | 450 |
| multi-bit | 317 | **23** |
| permutation | 195 | 50 |
| negation | 19 | 3 |
| identity | 10 | 10 |
| zero | 9 | 7 |

543 of 1841 matrices occur in more than one project, and 96% of those are shifts, extracts,
permutations or identities. Programs overwhelmingly perform bit manipulation that every ISA
already has an instruction for.

Only 23 non-trivial matrices recur across projects, and none appears in more than 4 of 16.

## What recurs, and why none of it is new

The 23 cross-project multi-bit matrices fall into three families.

### 1. Is any bit set

`@ABM` i64, 4 projects (luajit, lz4, oniguruma, wolfssl), 15 sites, runtime mask.

```
out[63..0] = x[31] | x[30] | ... | x[0]
```

Broadcasts "x is nonzero" to every bit. Already `test` on x86 and `snez` on RISC-V.

### 2. Highest set bit

`@ABM` i32, 3 projects (brotli, luajit, zstd), 21 sites, in `FSE_buildCTable_wksp`,
`FSE_buildDTable_internal` and `FSEv05_buildDTable`.

```
out[30] = x[31]
out[29] = x[31] | x[30]
out[28] = x[31] | x[30] | x[29]
...
```

A downward smear from the top set bit, which entropy coders use to size tables. This is `clz`,
which every target already provides.

### 3. XOR fold, and it is an artifact

`@ABMX` i8, 3 projects (libpng, wolfssl, zstd), 9 sites, runtime mask.

```
out[7..5] = x[7..5]
out[4] = x[7] ^ x[4]
out[3] = x[6] ^ x[3]
...
```

This looks like `x ^ (x >> 3)`, but the mask is a runtime value and it is `x` itself, so the fold
cancels and the cut it replaced is a single `lshr`. The same shape at i16 turns out to be a bare
`llvm.bswap.i16`. Ranking has to detect this, or the artifact outranks real operations.

## Candidates meeting the bar: none

```
abm-rank.py --all --candidates corpus.jsonl
# 0 candidate operation(s)
```

Nothing in 417 files is both non-trivial, needs 3 or more instructions, recurs across two
projects, and survives the mask-cancellation check. Relaxing to a single project yields 12, but
they are dense one-offs with arbitrary masks such as `0x3fd51d2f`, produced by matching only the
inputs a site can actually reach.

## The one real candidate found so far

Not from this corpus. zlib's `multmodp`, reached from `crc32_combine`:

**`@ABMX` i32, mask 0, 45 bits set.** One bit-serial CRC-32 step.

```
out[31] = x[0]
out[30] = x[31] ^ x[0]
out[29] = x[30] ^ x[0]
out[28] = x[29]
...          (x[0] is xored in wherever the polynomial has a bit)
```

Minimal implementation, 5 instructions, proved equivalent with `alive-tv`:

```llvm
%t0 = and  i32 %x, 1
%t1 = sub  i32 0, %t0
%t2 = and  i32 %t1, 3988292384    ; 0xEDB88320
%t3 = lshr i32 %x, 1
%t4 = xor  i32 %t2, %t3
```

540s of XOR-only search never found this. Recovering the matrix from basis vectors finds and
verifies it in seconds. No matrix of this shape appears anywhere in the 16-project corpus: the
crypto projects here use table-driven or hardware CRC.

## Reproducing

```bash
./build/abm-collect -o corpus.jsonl -t 240 -j 12 -a \
  -x '-minotaur-function-to=45' tmp-corpus/bench/*/optimized/*.ll

./build/abm-rank.py --all --candidates corpus.jsonl   # top operations
./build/abm-rank.py --all --by projects corpus.jsonl  # commonality
./build/abm-mincost.py --verify corpus.jsonl          # cost without ABM
```

## Limits

- Files still hit the per-file cap even with a 45s per-function budget, so results skew toward
  what is found early in a translation unit.
- `min_instructions` is exact only up to 2. Past that the search inverts the final instruction,
  and beyond 3 the reported count comes from a structural lowering and is an upper bound.
- A matrix need only agree with the original on reachable inputs. Known bits are recorded, but a
  matrix can still be denser than the operation really is.
