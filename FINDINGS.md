# Does mining affine bit-matrices find instructions worth adding?

**Short answer: mostly no.** Measured over 39,614 files from 248 projects, affine bit manipulation
is everywhere (2.9 million proved sites), but 94% of it already costs one or two instructions on
some mainstream ISA. An ABM instruction would save two or more instructions at 4.9% of sites (5.7%
in a typical project). Of the ones that recur across projects, two-thirds are a single C++ library
idiom (a clamp in `std::vector`'s growth check) rather than bit manipulation. What remains, about 1%
of sites, falls
into families that existing extensions or a general bit-matrix multiply (Cray BMM, GFNI) already
target: GF(2)-linear PRNG and hash steps, bit packing, and crypto mixing. The technique itself works: it finds real, nontrivial operations
(CRC steps, Morton codes, AES MixColumns, Reed-Solomon parity, xorshift chains) and proves them. It
just does not find many that are both frequent and expensive.

## Method

The earlier results in this file came from Minotaur's synthesis loop: an SMT search per cut, which
timed out on most files and only saw unary, same-width cuts of depth five. These results come from a
**census** instead (`-minotaur-abm-census`):

1. For every integer value, grow the largest pure expression DAG feeding it: bitwise ops, constant
   shifts and rotates, `bswap`/`bitreverse`, `zext`/`sext`/`trunc`, and the `add`/`mul`/`select`/
   `icmp` that are sometimes affine on reachable inputs. Up to 64 instructions, 12 operand edges.
2. Concatenate the DAG's inputs (up to 128 bits), so binary and width-changing operations are
   visible: `ABM(x ++ y)` is an `out x in` rectangular matrix.
3. Evaluate the DAG concretely. `b = f(0)`, `col_j = f(e_j) ^ b` gives the matrix. Screen it on 512
   inputs built to break linearity (equal inputs, sparse, dense, small, runs of ones, near the DAG's
   own constants), or on every reachable input when at most 12 bits are free. Known bits of the
   inputs constrain the samples, so a matrix holds on reachable inputs.
4. If the whole DAG is not affine, retry without arithmetic and then shallower. Log only maximal
   regions.
5. **Prove** every distinct region with `alive-tv` (`abm-verify.py`), against a shift/mask/xor form of
   the matrix, which the solver handles far better than the `@ABM` intrinsic (xorshift128+ as a
   64x128 matrix: timeout at 240s becomes instant). Operations refuted at any site are dropped.
6. Cost each operation on **x86-64-v4** (BMI2, GFNI, PCLMUL, AVX-512 VBMI/BITALG), **Armv9**
   (shifted operands, SVE2-BITPERM, SHA3, AES) and **RVA23** (Zbb, Zbkb, Zbkx, Zbc, Zknh) two ways:
   an oracle that recognizes the families those ISAs implement directly and otherwise bounds a
   shift/mask/combine network (`abm-isa.py`), and what `llc -O2` actually emits for the region
   (`abm-llc.py`). The lower of the two is used. Constants are free in the oracle, so it is
   generous to existing ISAs.
7. Charge an ABM `max(1, inputs - 1)` instructions, since a real instruction reads at most two
   registers. An operation is a candidate when an ABM saves at least 2 over the best ISA; saving 1
   is a two-instruction idiom that macro-op fusion already covers.

The census needs no SMT search, so it is fast: all 39,614 files took about an hour on 3 to 4 cores.
Proofs and llc then run once per distinct region: 102,458 regions, about three hours on 5 cores.
4,974 (4.9%) were refuted, which is what the structured screening leaves after random screening
alone let through 22% (mostly `icmp eq x, y`, which looks like the affine `(x|y) == 0` on random
inputs); 26 timed out.

## Corpus

- **llvm-opt-benchmark**: `bench/*/optimized` of 236 projects, 37,542 files of `-O3` IR (26 GB).
  `zed-rs` did not download.
- **Compiled scalar** with `clang -O2 -fno-vectorize -fno-slp-vectorize` (`abm-ext-corpus.sh`),
  2,072 files from 12 projects chosen for bit-manipulation density: PQClean (880), SoftFloat (345),
  MiBench (336), BearSSL (314), ISA-L (83), capstone (59), libcorrect (20), gf-complete (18),
  xxHash (9), BLAKE3 (5), Monocypher (2), Jerasure (1).

30 files could not be read: 21 broken by old-IR identifier collisions and 9 with other parse
errors.

3,679,278 records: 2,901,264 sites of 69,837 distinct affine operations (after dropping 2,933
refuted), plus 30,611 distinct small lookup tables.

## How much is already one instruction?

| class | sites | distinct | projects | sites an ABM saves >= 2 on |
|---|---|---|---|---|
| shift/extract | 1,563,367 | 19,992 | 247 | 165 |
| predicate (`x & m != 0`) | 585,574 | 14,685 | 244 | 0 |
| or-linear, 2+ inputs | 155,277 | 7,866 | 234 | 64,193 |
| sign-extend | 134,006 | 1,686 | 197 | 1,373 |
| merge | 106,518 | 2,298 | 182 | 2,952 |
| interleave/pack | 99,685 | 8,800 | 225 | 21,501 |
| xor-linear, 2+ inputs | 99,314 | 3,110 | 196 | 15,642 |
| xor-linear | 48,338 | 766 | 180 | 4,496 |
| broadcast | 44,885 | 6,650 | 218 | 8,987 |
| or-linear | 23,961 | 1,425 | 181 | 14,167 |
| parity | 18,872 | 1,142 | 183 | 2,873 |
| rotate | 9,823 | 228 | 122 | 490 |
| permutation | 8,987 | 729 | 127 | 3,334 |
| gather/scatter | 2,282 | 419 | 97 | 987 |
| GF(2^8)-linear, AES poly | 361 | 33 | 6 | 355 |
| GF(2^8)-linear, Reed-Solomon poly | 14 | 8 | 1 | 14 |

Instructions needed per site, as a share of sites:

| | <= 1 | 2 | >= 3 | <= 1 | 2 | >= 3 |
|---|---|---|---|---|---|---|
| | **all sites** | | | **projects weighted equally** | | |
| x86-64 | 43.5% | 42.0% | 14.4% | 38.5% | 46.9% | 14.6% |
| AArch64 | 48.6% | 45.4% | 6.0% | 45.5% | 46.8% | 7.7% |
| RISC-V | 39.2% | 45.1% | 15.7% | 35.4% | 47.8% | 16.9% |
| best of the three | 50.9% | 43.4% | 5.7% | 47.9% | 44.7% | 7.4% |

AArch64 is closest to "everything is one or two instructions", because its shifted operands fold a
shift or rotate into `eor`/`orr`/`and` and `ubfx`/`sbfx`/`bfi` cover field moves. What `llc -O2`
emits today is worse than the oracle's estimate (best target: 45.0% / 40.2% / 14.8%), partly from
materializing constants and partly from instructions it does not select.

## What an ABM would save

An ABM saves at least two instructions at 141,529 sites (4.9%). Summed over all sites, it would
save 1,640,319 instructions against the best ISA, 0.57 per site.
Most of that is the 2 -> 1 kind: predicates alone are 32%, shifts and extracts 24%. Restricted to
operations an ABM does in at least two fewer instructions, the saving is 383,765 instructions (23%
of the total), and only 2.5% of the total once weighted by static block frequency, because those
operations mostly sit outside hot loops.

## The residue

1,195 operations save at least two instructions on every ISA and recur in at least two projects:
89,736 sites, 3.1% of all sites. By family:

| family | operations | sites | share | projects |
|---|---|---|---|---|
| select / clamp | 412 | 61,291 | 68.3% | 189 |
| pack / interleave | 228 | 8,691 | 9.7% | 131 |
| other XOR-linear (PRNG, CRC, hash) | 90 | 5,692 | 6.3% | 114 |
| OR-linear, 2+ inputs | 100 | 3,526 | 3.9% | 88 |
| OR-linear | 61 | 2,780 | 3.1% | 95 |
| permutation | 22 | 2,312 | 2.6% | 39 |
| parity | 126 | 1,206 | 1.3% | 90 |
| everything else | 156 | 4,238 | 4.7% | |

- **select / clamp** is mostly one idiom: `select(c, MAX, x) << s`, the size clamp in libstdc++'s
  `vector::_M_check_len`, in 103 projects. On reachable inputs it is `(x | broadcast(c)) << s`,
  which is affine, but it is a conditional clamp, not something an ISA designer would call bit
  manipulation. `csel` handles it in two or three instructions.
- **pack / interleave** is mostly four bytes assembled into a word, from four loads. In memory those
  bytes are usually adjacent, so a load plus `bswap` (or `movbe`) does it; LLVM just did not combine
  the loads. The rest, such as UTF-8 decoding in fmt (bundled into 14 projects, 9 instructions on
  the best ISA against 5 two-input ABMs), is a real bit-field gather from several registers.
- **XOR-linear** is where the real candidates are. **Mersenne Twister tempering** (`std::mt19937`
  and `mt19937_64`, 41 projects) is a 32x32 or 64x64 GF(2) matrix that costs 9 to 13 instructions
  on the best ISA, and one ABMX. **xorshift** chains (Stockfish, Rust's `sort` pattern breaker),
  CRC steps and hash mixers are the same shape. This is the one family where a general GF(2)
  matrix multiply would clearly beat existing ISAs, but it accounts for 6.3% of the residue and
  0.2% of all sites.
- **Crypto mixing** (XOR of rotations over three or four words: ChaCha, SHA-1, SM3, Blowfish, ARIA)
  is small here, because most of these already have dedicated instructions (SHA, SM3, Zknh/Zksh) or
  three-input XOR (`vpternlog`, `EOR3`).
- **GF(2^8)-linear** maps (AES MixColumns, Reed-Solomon parity) are found and proved, but are rare
  in this corpus, and AES has instructions on every mainstream ISA.

Small lookup tables (non-affine regions with at most 8 free input bits) are the same story:
predicates on two or three bits, character-class range checks (`c - '0' < 10`), and one-hot decoding
(`1 << (x & 63)`).

## What the technique does find

These are proved, and none needed a hand-written pattern:

- **Morton interleave** of two 16-bit values: 28 instructions, one 32x64 matrix.
- **zlib's bit-serial CRC-32 step** (`multmodp`), and the whole `crc32_combine` operator in synthesis
  mode.
- **AES MixColumns** in any byte order (spike's vector AES, BearSSL), recognized as GF(2^8)-linear
  over bytes, and **RAID-6 P+Q parity** in ISA-L (Reed-Solomon coefficients 1, 2, 4 over 0x11D).
- **Three unrolled xorshift steps** in Stockfish's magic-number setup: nine instructions, one 64x64
  GF(2) matrix, nine instructions on the best ISA today.
- **DES bit permutations done with `mul`** in BearSSL: masking makes the multiply a pure bit
  gather, which the census sees as a permutation.
- **Bitsliced AES transposition** (`br_aes_ct_ortho`) and **Kyber coefficient packing**.

## Cross-check with synthesis and the cost function

The census does not rewrite anything; Minotaur's synthesis mode does, and the ABM cost decides what
it accepts. On eight files chosen for bit-manipulation density (zlib CRC, Stockfish bitboards, ISA-L
RAID and erasure coding, BearSSL `aes_ct` and `des_ct`, libsodium ChaCha20, Kyber `poly`):

| `-minotaur-abm-cost` | accepted | candidates | extracted |
|---|---|---|---|
| 1 (beats any single instruction) | 98 | 1132 | 228 |
| 5 (must replace about three) | 40 | 205 | 241 |

At cost 5 the accepted matrices are Kyber bit packing, bitsliced AES transposition and key
schedule, ISA-L GF(2^8) multiplication by constants, zlib's CRC combine step, and Stockfish's bitboard
setup -- the same families the census residue contains.

## Prior art

ABM as an operation is not new, which is why the contribution has to be the measurement:

- **Cray BMM** (patented 1990): bit-matrix multiply on operands up to 64x64.
- **Hilewitz, Lauradoux and Lee**, "Bit matrix multiplication in commodity processors" (2008), and
  their pex/pdep and butterfly-permutation work, which became BMI2's `pext`/`pdep`.
- **RISC-V draft Zbm** (bitmanip 0.9x): `bmatxor`, `bmator`, `bmatflip` on 8x8 matrices. Dropped
  before the Zb* extensions were ratified in 2021.
- **Intel GFNI**: `gf2p8affineqb` is an 8x8 GF(2) affine map per byte; `gf2p8mulb` multiplies bytes
  in GF(2^8).

## Limits

- The census only sees **scalar** integer code. `llvm-opt-benchmark` is `-O3` IR, so bit tricks in
  vectorized loops are invisible; the 12 projects compiled here with `-fno-vectorize` partly cover
  that.
- **Frequency is static**: `BlockFrequencyInfo` without profiles, relative to function entry, and
  heavy-tailed (one gf-complete loop dominates some weighted totals). Static site counts are the
  primary measure here.
- **Only maximal regions** are logged. An interesting sub-operation inside a larger affine region is
  counted as part of it.
- The oracle is approximate in both directions. It ignores constants (generous to ISAs), and its
  recognizers miss some idioms; `llc`'s output caps it from above, but neither is an optimal
  superoptimization of each region.
- Loads are inputs. Byte packing from four adjacent loads is a single `load`+`bswap` in practice;
  the census cannot see adjacency, so those regions overstate the residue.
- 30 files failed to parse (identifier collisions in old IR, among others).

Results from the earlier synthesis-based runs (28 projects, predicate broadcast and small-domain
tables) are in this file's history at `12b9d6d`; the census reproduces them as the predicate and
table classes.

## Reproducing

```bash
# corpora: llvm-opt-benchmark (sparse checkout of bench/*/optimized) and the scalar builds
find tmp-corpus-all/bench corpus-ext/bench -name '*.ll' > files.txt
systemd-run --user --scope -p MemoryMax=10G \
  ./build/abm-pipeline -o runs -f files.txt -j 4 -m 2048 -p 2048
less runs/report.txt
./build/abm-isa.py --self-test
```
