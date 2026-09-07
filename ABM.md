# Discovering novel bitmanipulation instructions

## Abstract
Specialized hardware for AI, cryptography, and other computation requires specialized instruction sets for the specific use case. Processor designers have the problem of providing an instruction set architecture (ISA) that is manageable but meets performance needs. A new instruction or intrinsic should meet two criteria: (1) it should fill a gap in the existing ISA, and (2) it should be sufficiently common so as to justify reservation in encoding space. This paper proposes a method for synthesizing a class of bit manipulation instructions that can be expressed as an affine transformation of a bit vector. We use Minotaur, an LLVM superoptimizer, to automatically express sequences of instructions as these transformations, then determine which matrices are most common. This method is effective for finding what kind of bit manipulation real programs are doing.

## Definition of semantics for affine bit manipulation instructions

The design for this instruction is partially inspired by Intel’s GF2P8AFFINEQB (Galois Field Affine Transformation) instruction. It's implemented as a special case in Alive2 for a call to a function called “ABM” (“affine/arbitrary bit manipulation”) with the following or similar signature in LLVM IR:
declare i8 @ABM(i8, i64, i8)
The first argument is an n-bit vector x. The second argument is the n by n (n^2-bit) matrix M. The third argument is the n-bit mask b. Then, the operation is defined by M\*x+b. Where \* is a bit matrix product with bitwise XOR for intermediate sums.

Note that M and b are not meant to be immediates in the encoding of the eventual machine instruction. ABM is a search space, not an instruction: a given (M, b) pair *is* the semantics of one candidate unary instruction, which hardware would implement directly. So the n^2 bits are a description of a transformation we are trying to discover, and their size does not constrain the encoding of the instruction that comes out of the search.

### Two semirings

Neither choice of intermediate sum spans the transformations we care about, so both are implemented as separate intrinsics and enumerated together:

- `@ABM` sums with **OR** (a boolean matrix product). Reaches `orc.b`-style reductions, "is any bit in this range set", and two's complement negation.
- `@ABMX` sums with **XOR** (the GF(2) product above, as in GF2P8AFFINEQB). Reaches carry-less multiply, CRC, parity and gray code.

Because they are distinct intrinsics rather than one operation with a symbolic mode bit, the solver never branches on which semiring is in play — the choice is fixed while the formula is built. `-minotaur-abm-mode=or|xor|both` selects which are enumerated (default `both`).

XOR is much harder to solve: each output bit becomes an n-term parity. Measured on an `fshl` cut, XOR is instant at i8, ~1s at i16, ~6s at i24 and does not finish at i32, while OR is instant through i32. `@ABMX` therefore has its own, lower width cap (`-minotaur-abm-xor-max-width`, default 16) alongside `-minotaur-abm-max-width` (default 64).

### The mask

`b` may be either a constant or a runtime value; both are recorded, and a runtime mask is logged as `"mask": null`. This matters: negation is only expressible as `ABM(x, borrow_chain, x)`, where the mask carries the XOR with `x`. On ties the enumerator prefers a constant mask, because a rewrite that reuses a live value always has an equivalent constant-mask form and only the latter names a matrix comparable across programs.

### Recovering the matrix instead of searching for it

ABM is linear, which makes the search unnecessary. On a basis vector `e_j` the
matrix product selects exactly column `j`, and a single term leaves nothing for
OR or XOR to combine, so

    b     = f(0)
    col_j = f(e_j) ^ b

recovers `M` and `b` from `n + 1` evaluations of a cut, identically for both
semirings. The semirings are then told apart on inputs with more than one bit
set. Since the cost no longer depends on the width of the matrix, the width caps
above stop being the limiting factor: the bit-serial GF(2) step in zlib's
`multmodp`, `(x >> 1) ^ (0xEDB88320 if x & 1)`, is an i32 `@ABMX` with mask 0
that 540s of XOR-only search never found and that extraction recovers, verifies
and applies in seconds.

## What makes a matrix a candidate

A fixed `(M, b)` in hardware is an AND array feeding an OR or XOR reduction
tree: single cycle, no carry chain, no state. So its cost is 1 by premise, every
candidate is cheap by construction, and **cost cannot discriminate between
candidates**. Matrix structure matters to area but not much to latency, so it is
not an interestingness signal either.

What earns encoding space is instead

    frequency x savings,   savings = (cheapest ABM-free implementation) - 1

Both factors are measured: `rel_freq` and site counts give the first,
`scripts/abm-mincost.py` gives the second. Matrices needing one or two ordinary
instructions, which is most of them, are already covered by existing ISAs.

## Minotaur Patch

First, we will have to patch Minotaur's enumerator to always consider the instruction and to synthesize constants for the matrix and mask. Then, Minotaur just needs to be built against the modified Alive2, and it can start considering correct optimizations that use the instruction.
However, Minotaur’s cost model still doesn’t understand this instruction. We don’t want the optimizer to replace just one or two instructions, but it should replace three-instruction sequences. Once the cost is tuned, we should be able to run Minotaur on some programs of interest.

## Gathering data on common transformations

We’ll keep track of each transformation that Minotaur considers (along with the instructions it replaced to preserve the original intention of the program), and rank matrices based on their commonality. Ranking can be done across all programs, but recall that the instructions we are interested in discovering are those which are useful to specialized domains. For example, when looking for video transcoding instructions, we can combine data from FFmpeg and Libvpx. We can then look through the most popular operations and match them with the original program intent to determine if any could be candidates for new bit manipulation instructions.


## Commands

```bash
# example of getting a corpus
cd ~/research/minotaur
git clone --filter=blob:none --no-checkout --depth 1 \
  https://github.com/dtcxzyw/llvm-opt-benchmark.git tmp-ffmpeg
cd tmp-ffmpeg && git sparse-checkout set --no-cone bench/ffmpeg/optimized && git checkout

# run
ls tmp-ffmpeg/bench/ffmpeg/optimized/*.ll | shuf > files.txt      # seeded: shuf --random-source=<(yes 7)
timeout 18000 ./build/abm-collect -o abm.jsonl -t 120 -j 8 $(cat files.txt)

# count and hex of most common matrices
jq -r '"\(.width) \(.matrix)"' abm.jsonl |
  sort | uniq -c | sort -rn | head |
  awk '{printf "%6d  i%-3s 0x%.14s…\n", $1, $2, $3}'

./build/abm-rank.py abm.jsonl                        # full report
./build/abm-rank.py --top 12 --src-lines 0 abm.jsonl # just the head
./build/abm-rank.py --summary abm.jsonl              # per-output-bit, not a grid
./build/abm-rank.py --multi-bit --summary abm.jsonl  # <- the research query
./build/abm-rank.py --json abm.jsonl | jq .          # machine-readable
```
