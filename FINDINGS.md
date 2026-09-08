# What operations do programs actually perform?

Corpus: **28 projects, 801 optimized IR files** from `llvm-opt-benchmark`, spanning compression
(zstd, brotli, libdeflate, lz4, zfp), crypto (libsodium, wolfssl), codecs (libjpeg-turbo, libwebp,
libpng, lodepng, flac), text and fonts (harfbuzz, freetype, opencc, tokenizers-rs,
unicode-normalization-rs), scanning (re2, oniguruma, zxing, jq), runtimes (lua, luajit, quickjs),
plus mimalloc, sqlite, softposit-rs and base64-rs.

Runs: `abm-collect -j 12 -a -x '-minotaur-function-to=45'`, about 4h total.
**35793 records** (3528 accepted, 23921 candidate, 8344 extracted), **2339 distinct matrices**.
Semirings: 22074 OR, 13719 XOR. Widths: i8 5487, i16 1375, i32 19011, i64 9496.

## The distribution

| class | matrices | in 2+ projects |
|---|---|---|
| shift / extract | 1558 | 581 |
| multi-bit | 444 | 44 |
| permutation | 292 | 78 |
| negation | 26 | 5 |
| identity | 10 | 10 |
| zero | 9 | 9 |

727 of 2339 matrices occur in more than one project. Most of that is shifts and extracts, but
grouping only by matrix shape hides the two classes below, which cut across it.

## 1. Predicate broadcast

**89 matrices, 547 sites, 22 of 28 projects.** The single most widespread non-trivial operation.

Every output bit is the same boolean, so the operation is "materialise a mask from a predicate".

| predicate | result | projects | sites | seen in |
|---|---|---|---|---|
| 1 bit | 32 bits | 7 | 28 | brotli, jq, libjpeg-turbo, libwebp, luajit |
| 1 bit | 2 bits | 5 | 34 | freetype, jq, lua, luajit, zfp |
| 32 bits | 5 bits | 5 | 31 | flac, libjpeg-turbo, luajit, zstd, zxing |
| 32 bits | 32 bits | 3 | 38 | libjpeg-turbo, libsodium, wolfssl |
| 1 bit | 64 bits | 4 | 8 | libjpeg-turbo, mimalloc, opencc, zstd |

ARM spells this `CSETM`; x86 fakes it with `SBB`. It shows up in constant-time crypto, bitmap
allocators, entropy coders and JIT code alike.

## 2. Small-domain tables

**120 matrices, 299 sites, 20 projects.** A matrix reading k input bits is a 2^k entry lookup no
matter how wide the value is.

The best is a **byte to 32-bit table in two independent crypto libraries**:

```
f(byte) = 0xFFFFFFFF if byte == 0 else 0
```

`@ABM` i32, 8 input bits, mask `0xFFFFFFFF`, in wolfSSL `wc_RsaUnPad_ex` and libsodium
`sodium_memcmp`. `known_zero=0xFFFFFF00` confirms the input is a byte. The constant-time zero mask,
and getting it wrong is a padding-oracle bug.

Three instructions, proved equivalent with `alive-tv`:

```llvm
%t = and  i32 %x, 255
%u = add  i32 %t, -1
%r = ashr i32 %u, 31
```

The same family appears as `(x & 0xFF) != 0 ? 2 : 1` (from `(b + 511) >> 8`) across
`sodium_memcmp` and `sodium_is_zero`, 8 sites, also verified.

Others worth naming:

- **16 entry table**, wolfssl and zxing: `-(x mod 16)`, the GTIN check digit and a wolfSSL
  benchmark.
- **256 entry i8 tables**, 4 projects each. Most are `x ^ (x >> k)` with a runtime mask that is
  `x` itself, so the fold cancels and the cut is a bare shift. `--tables` now annotates these
  ("with mask = x: reduces to a shift/extract") rather than dropping them, so they can be judged
  rather than hidden.

## 3. The bit-serial CRC-32 step

Not from this corpus. zlib's `multmodp`, reached from `crc32_combine`.

**`@ABMX` i32, mask 0, 45 bits set.** Five instructions, proved with `alive-tv`:

```llvm
%t0 = and  i32 %x, 1
%t1 = sub  i32 0, %t0
%t2 = and  i32 %t1, 3988292384    ; 0xEDB88320
%t3 = lshr i32 %x, 1
%t4 = xor  i32 %t2, %t3
```

540s of XOR-only search never found it. Recovering the matrix from basis vectors finds and verifies
it in seconds. Nothing of this shape appears in the 28 project corpus: the crypto projects here use
table-driven or hardware CRC.

## Reproducing

```bash
./build/abm-collect -o corpus.jsonl -t 180 -j 12 -a \
  -x '-minotaur-function-to=45' tmp-corpus/bench/*/optimized/*.ll

./build/abm-rank.py --all --tables corpus.jsonl        # small-domain operations
./build/abm-rank.py --all --by projects corpus.jsonl    # commonality
./build/abm-rank.py --all --candidates corpus.jsonl     # 3+ instruction candidates
./build/abm-mincost.py --verify corpus.jsonl            # cost without ABM
```

## Limits

- The instruction counts from `abm-mincost.py` have no `icmp`, `select` or `zext` in the operator
  set, so anything involving a comparison or a widening falls back to a structural lowering and is
  badly overstated. The constant-time mask above reports 39 and is actually 3.
- Files still hit the per-file cap even with a 45s per-function budget, so coverage skews toward
  what is found early in a translation unit.
- A matrix need only agree with the original on reachable inputs. Known bits are recorded, and for
  small input domains the matrix is a genuine table rather than an artifact, but a wide matrix can
  still be denser than the operation really is.
