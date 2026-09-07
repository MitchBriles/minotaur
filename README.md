# Minotaur: a Synthesizing Superoptimizer for LLVM

[![Build](https://github.com/minotaur-toolkit/minotaur/actions/workflows/build.yml/badge.svg)](https://github.com/minotaur-toolkit/minotaur/actions/workflows/build.yml)

A description of how Minotaur works can be found in
https://arxiv.org/abs/2306.00229.

## Build Minotaur using Docker

To build the docker container, use

    docker build -t minotaur-dev -f Dockerfile .

To run the container, use

    docker run -it minotaur-dev bash

## Build Minotaur from source code

Minotaur requires `cmake`, `ninja-build`, `gcc-10`, `g++-10`,
`redis`, `redis-server`, `libhiredis-dev`, `libbsd-resource-perl`,
`libredis-perl`, `libgtest-dev` and `re2c` as dependencies. Minotaur/Alive2
also require Z3; we recommend building Z3 from source at tag `z3-4.15.4`. On
Ubuntu/Debian, use

    sudo apt-get install cmake ninja-build gcc-10 g++-10 redis redis-server libhiredis-dev libbsd-resource-perl libredis-perl re2c libgtest-dev

or on mac, use

    brew install cmake re2c hiredis redis

to install dependencies.

Build and install Z3 from source (tag `z3-4.15.4`).

    git clone --depth 1 --branch z3-4.15.4 https://github.com/Z3Prover/z3.git $HOME/z3
    cmake -S $HOME/z3 -B $HOME/z3/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$HOME/z3/install -DZ3_BUILD_TEST_EXECUTABLES=OFF -DZ3_BUILD_EXECUTABLE=ON
    cmake --build $HOME/z3/build --target install

Clone Minotaur repository with

    git clone git@github.com:minotaur-toolkit/minotaur $HOME/minotaur

The Alive2 requires a LLVM compiled with RTTI and exceptions enabled,
use the following command to fetch and build LLVM.

    git clone --depth=1 git@github.com:llvm/llvm-project $HOME/llvm
    cd $HOME/llvm && git apply $HOME/minotaur/llvm-main-minotaur.patch
    mkdir $HOME/llvm/build && cd $HOME/llvm/build
    cmake -GNinja -DLLVM_ENABLE_RTTI=ON -DLLVM_ENABLE_EH=ON -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo -DLLVM_TARGETS_TO_BUILD="X86;AArch64;RISCV" -DLLVM_ENABLE_ASSERTIONS=ON -DLLVM_ENABLE_PROJECTS="llvm;clang" ../llvm
    ninja

To fetch and build the Alive2, use

    git clone --depth=1 git@github.com:alivetoolkit/alive2.git $HOME/alive2
    mkdir $HOME/alive2/build && cd $HOME/alive2/build
    cmake -GNinja -DLLVM_DIR=$HOME/llvm/build/lib/cmake/llvm -DBUILD_TV=1 -DCMAKE_EXPORT_COMPILE_COMMANDS=1 -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_PREFIX_PATH=$HOME/z3/install ..
    ninja

To build Minotaur, use

    mkdir $HOME/minotaur/build && cd $HOME/minotaur/build
    cmake .. -DALIVE2_SOURCE_DIR=$HOME/alive2 -DALIVE2_BUILD_DIR=$HOME/alive2/build -DCMAKE_PREFIX_PATH="$HOME/llvm/build;$HOME/z3/install" -DCMAKE_EXPORT_COMPILE_COMMANDS=1 -DCMAKE_BUILD_TYPE=RelWithDebInfo -G Ninja
    ninja

To run the test suite, use

    ninja check

## Use Minotaur

Minotaur can be invoked in two ways, it can be invoked online (during
compilation), but also offline, in a mode where Minotaur extracts cuts
into the redis cache but does not perform synthesis. In offline mode,
a separate program called `cache-infer` retrieves cuts from the cache,
runs synthesis on them, and stores any optimizations that it discovers
back into the cache. Unlike the online mode, which runs synthesis
tasks one after the other, offline mode can run all synthesis jobs in
parallel.


### Online mode

To run the Minotaur on llvm IR files in online mode, use the following
command

    $HOME/llvm/build/bin/opt -load-pass-plugin $HOME/minotaur/build/online.so -passes="minotaur" <LLVM bitcode>

For C/C++ programs, we have a drop-in replacement of C/C++ compiler.
Users can call `minotaur-cc` or `minotaur-c++` in the `build`
directory to compile C/C++ programs. Minotaur pass is disabled by
default; the pass can be enabled by setting environment variable
`ENABLE_MINOTAUR`.

    export ENABLE_MINOTAUR=ON
    $HOME/minotaur/build/minotaur-cc <c source> [clang options]

### Offline mode

#### Extract cuts from source

To extract cuts, one can just set the environment variable
`MINOTAUR_NO_INFER` and run the same command as above.

For a single LLVM IR source,

    MINOTAUR_NO_INFER=ON $HOME/llvm/build/bin/opt -load-pass-plugin $HOME/minotaur/build/online.so -passes="minotaur" <LLVM bitcode>

For a single C/C++ source,

    ENABLE_MINOTAUR=ON MINOTAUR_NO_INFER=ON $HOME/minotaur/build/minotaur-cc <c source> [clang options]

For a C/C++ project, simply set CC and CXX to `minotaur-cc` and `minotaur-c++`, and run `configure` as usual. Set `ENABLE_MINOTAUR` and `MINOTAUR_NO_INFER` to `ON` for `make`.

    CC=$HOME/minotaur/build/minotaur-cc CXX=$HOME/minotaur/build/minotaur-c++ ./configure
    ENABLE_MINOTAUR=ON MINOTAUR_NO_INFER=ON make

#### Run synthesis on cuts

Run the `cache-infer` program to retrieve cuts from the cache and run
synthesis on them.

    $HOME/minotaur/build/cache-infer

After running `cache-infer`, the cache will be populated with the
optimizations that Minotaur discovered. User can run the `opt`,
`minotaur-cc`, `minotaur-c++` or `make`  again, to compile the program
with the synthesized optimizations.

### Dump synthesized results from cache

To dump synthesized results from cache, use `cache-dump`.

    $HOME/minotaur/build/cache-dump

### Ranking ABM matrices

Minotaur can synthesize `ABM(x, M, b)`, the affine bit-manipulation operation
described in `ABM.md`, which computes `M*x + b` over GF(2). To find out which
matrices programs actually use, pass `-minotaur-abm-log` and Minotaur appends
one JSON record per synthesized ABM to that file:

    $HOME/llvm/build/bin/opt -load-pass-plugin $HOME/minotaur/build/online.so \
      -passes=minotaur -minotaur-abm-log=abm.jsonl <LLVM bitcode>

Records are appended, never truncated, and each line is written whole, so
parallel builds can share one log. `abm-collect` wraps this for a list of IR
files:

    $HOME/minotaur/build/abm-collect -o abm.jsonl tests/abm/*.ll

`-x` passes extra flags through to `opt`, which is how the semiring and width caps are selected
for a run:

    $HOME/minotaur/build/abm-collect -o abm.jsonl -a \
      -x '-minotaur-abm-mode=xor -minotaur-abm-xor-max-width=32' foo.ll

By default only the rewrite that was actually applied is logged, read off the
finished function so that a matrix later folded into a wider one is not counted
alongside its own replacement. Adding `-a` (or `-minotaur-abm-log-all`) also
records verified rewrites that lost on cost, as `kind="candidate"`.

Minotaur also recovers ABM matrices without searching for them. ABM is linear,
so on a basis vector the matrix product selects exactly one column and there is
nothing for OR or XOR to combine:

    b     = f(0)
    col_j = f(e_j) ^ b

`n + 1` evaluations of a cut therefore give `M` and `b` outright, for either
semiring, and the two are told apart by testing inputs with more than one bit
set. This is on by default (`-minotaur-abm-extract=false` turns it off, which is
useful for comparing against pure enumeration). Because the cost no longer
depends on the width of the matrix, it reaches cases the solver cannot: the
bit-serial CRC-32 step in zlib's `multmodp` is an i32 `@ABMX` with mask 0, and
540s of XOR-only search never found it while extraction recovers and verifies it
in seconds.

Extraction results are logged as `kind="extracted"` with an `evidence` field,
separately from `accepted`, because a wide XOR matrix is often computed exactly
even when proving it is beyond the solver's budget.

Long functions can starve the rest of a translation unit, so
`-minotaur-function-to=SECS` bounds time per function (0, the default, means no
limit). With it `crc32.ll` finishes in 91s and yields records from four
functions; without it the file runs past 600s and only ever reports `crc32_z`.

`abm-rank.py` groups the log on (width, matrix) and ranks by commonality:

    $HOME/minotaur/build/abm-rank.py abm.jsonl

    # 11 accepted record(s), 9 distinct matrix/matrices

    #1  count=3  width=8  matrix=0x1020408102040
        0 1 0 0 0 0 0 0
        0 0 1 0 0 0 0 0
        ...
        mask: 0x00 x3
        from: addself @ tests/abm/addself.ll, mul2 @ ..., shl1 @ ...

`--interesting` keeps only matrices that need more than one ordinary
instruction, dropping the identities, shifts, extracts, permutations and
negations that existing ISAs already cover; `--extracted` selects only
basis-vector results. Every entry reports its `class`.

Useful flags: `--all` to include candidates and extractions, `--top N`,
`--color`, `--llvm` to
print the matrix as an LLVM vector constant, and `--json`. Matrices are laid
out with row 0 on top as the most significant bit of the result, i.e.
`out[N-1-i] = OR over j of M[i*N + j] & x[j]`.

There are two intrinsics, enumerated together by default: `@ABM` sums the
matrix product with OR, `@ABMX` with XOR. `-minotaur-abm-mode=or|xor|both`
selects which. Each has its own width cap, because XOR turns every output bit
into a parity and is far harder to solve: `-minotaur-abm-max-width` (default
64) and `-minotaur-abm-xor-max-width` (default 16).

Each record also carries `loop_depth` and `rel_freq` (the containing block's
frequency relative to function entry), so a site in a hot loop can outweigh one
on a cold path:

    abm-rank.py --by freq abm.jsonl     # or: --by count (default), --by depth
    abm-rank.py --op xor abm.jsonl      # only @ABMX records

Each accepted record also carries `known_zero` / `known_one` for the ABM's input,
from `computeKnownBits`. A matrix only has to agree with the original on
reachable inputs, so without this a rewrite can look far stranger than the
operation really is: the smear accepted in zlib's `deflateInit2_` is just
`x - 1` on the powers of two that reach it.

An ABM is one cheap unary instruction by premise, so its cost is 1 and cannot
discriminate between candidates. What decides whether a matrix deserves opcode
space is `frequency x savings`, where savings is the cost of the cheapest
ABM-free implementation minus one. `abm-mincost.py` measures that second factor:

    $HOME/minotaur/build/abm-mincost.py --verify abm.jsonl

    @ABM  i32  count=4  min=3 instruction(s)
        matrix=0x100000002000000040000000...  mask=0x8
          add %v, 7
          and %v, 7
          add %v, 1
        alive-tv: verified

Depth 0 to 2 is exhaustive, so "needs 3 or more" is exact, which is the bar that
matters. Depth 3 is reached by inverting the final instruction, so a reported 3
is real while "more than 3" is a lower bound. `--verify` proves the winning
sequence with `alive-tv`, and `--ignore-preconditions` judges a matrix on all
inputs rather than only reachable ones.

`rel_freq` comes from LLVM's `BlockFrequencyInfo`. Without profile metadata
that is a static loop-nesting estimate rather than measured frequency -- useful
as a proxy, but not a substitute for a real profile. It improves automatically
on IR that carries `!prof`. Logs written before these fields existed still
rank fine; they fall back to `op=or`, `rel_freq=1.0`, `loop_depth=0`.

While iterating on ABM, `abm-check` runs just the scalar integer tests -- the
only ones ABM enumeration can reach, since floating-point and vector cuts skip
it -- in about 15 seconds, rather than the much longer full `ninja check`:

    $HOME/minotaur/build/abm-check

`-minotaur-abm-max-width=0` turns ABM enumeration off entirely, which is a
quick way to tell whether a change in behaviour is coming from ABM or from
somewhere else.

`tests/abm/` holds small `i8` samples covering shifts, rotates and
`bitreverse`. Note that the matrix product sums with OR while the mask is
applied with XOR, so an XOR-linear transform such as `x ^ (x >> 3)`
(`xorshift.ll`, `clmul3.ll`) is matched by putting the XOR in the mask operand:
the result is `ABM(x, M, %x)` with a runtime mask, whose matrix describes only
the shift. Such records are logged with `"mask": null`.
