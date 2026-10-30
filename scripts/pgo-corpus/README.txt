Profiled corpus recipes (ohm: ~/abm-work/pgo)
============================================

Goal: for each project, build it twice with a wrapper compiler and run a
realistic workload in between, so that we get LLVM IR annotated with real
execution counts (PGO profiles). The IR lands in ~/abm-work/pgo/ir/<project>/.
We use it to find which bit-manipulation operations sit on hot paths.

Layout
  lib.sh                 shared harness (do not edit)
  recipes/<project>.sh   one recipe per project (you write these)
  src/<project>          source checkout (fetch puts it here)
  build/<project>        build dir for out-of-tree builds (wiped per phase)
  data/                  shared inputs. text64m = 64 MB of C++ source text.
                         Put any generated inputs here (wav, yuv, ...).
  logs/<project>.{gen,run,use}.log
  ir/<project>/*.ll      the result

A recipe defines three bash functions, then sources lib.sh and calls pgo_main:

  #!/bin/bash
  # <project>: one line saying what the workload exercises
  fetch()    { git clone -q --depth 1 URL "$SRC"; }
  build()    { ...build using "$CC"/"$CXX" with -j"$J"; put binaries in "$BLD"... }
  workload() { ...run binaries from "$BLD" on inputs in "$DATA"... }
  source "$HOME/abm-work/pgo/lib.sh"
  pgo_main

pgo_main runs build twice (ABM_PHASE=gen, then ABM_PHASE=use). The wrapper
compilers in $CC/$CXX add -O2 -fno-vectorize -fno-slp-vectorize, the profile
flags and IR emission themselves; do not add optimization flags of your own and
do not use ccache. Rules for build():
  - It must work from scratch both times. For in-tree builds (make/autotools
    in $SRC), call fresh_src first; it runs git clean -fdx in $SRC.
  - Make sure the project's build really uses $CC/$CXX (CMake:
    -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX"; make: CC="$CC";
    autotools: ./configure CC="$CC" CXX="$CXX").
  - Prefer portable C code paths: disable assembly and SIMD/intrinsics options
    when the project has them (e.g. --disable-asm, -DWITH_SIMD=OFF,
    ARCH=x86-64 generic). We want the scalar C implementation profiled.
  - Static, no shared libs if easy; no tests or docs builds unless the workload
    needs them.
workload() runs with the profile env set; it should take 10 s to 5 min
(instrumented), and must exercise the program's real hot paths with realistic
inputs (a benchmark mode of the program itself is ideal). Use only binaries
built in this recipe. It must exit 0.

Run a recipe on ohm:
  chmod +x ~/abm-work/pgo/recipes/<p>.sh
  ~/abm-run pgo-<p> ~/abm-work/pgo/recipes/<p>.sh       # in tmux, capped
  tail ~/abm-logs/pgo-<p>.log                           # ends "EXIT <rc>"
Success is a last line "DONE <p> ir_files=N with_profile=M ..." with M > 0.

HARD RULES (shared 64-core machine, memory limit is strict)
  - Run every build/workload through ~/abm-run (it puts the job in a cgroup
    slice capped at 180 GB shared with other jobs). Never run builds directly
    over ssh outside ~/abm-run.
  - At most 2 of your recipes running at once. J defaults to 24; keep it.
  - Never wait on a job with an unbounded loop; poll the log a few times with
    sleeps of at most 60 s, and give up on a recipe after ~3 failed attempts or
    45 minutes; record it as skipped with the reason.
  - Do not touch anything outside ~/abm-work/pgo except reading. Do not touch
    ~/alive2, ~/minotaur, ~/llvm-project, or other people's processes.
  - Network: GitHub and project websites are reachable from ohm.

Paths: lib.sh, test-suite.sh and census-run.sh assume ~/abm-work for the
corpus, ~/minotaur for this repository, ~/llvm-project/build for LLVM and
~/alive2-abm/build for alive-tv (census-run.sh takes MINOTAUR, ALIVE_TV and
LLC from the environment). The 2026-10-29 run used 296 test-suite benchmarks
(test-suite.sh) and 33 of the 34 recipes here; rtweekend was left out
because its current version draws from rand(), not std::mt19937.
