#!/bin/bash
# LLVM test-suite benchmarks with their reference inputs, profiled per benchmark.
set -uo pipefail
W=$HOME/abm-work
TS=$W/test-suite
export PATH=$HOME/llvm-project/build/bin:$PATH
export ABM_CLANG=$HOME/llvm-project/build/bin/clang ABM_CLANGXX=$HOME/llvm-project/build/bin/clang++
CC=$HOME/minotaur/scripts/abm-pgo-cc CXX=$HOME/minotaur/scripts/abm-pgo-c++
common=(-G Ninja -DCMAKE_C_COMPILER=$CC -DCMAKE_CXX_COMPILER=$CXX
  -DTEST_SUITE_BENCHMARKING_ONLY=ON -DTEST_SUITE_SUBDIRS="SingleSource;MultiSource"
  -DTEST_SUITE_PROFILE_DIR=$W/ts-prof -DTEST_SUITE_COLLECT_CODE_SIZE=OFF
  -DTEST_SUITE_LIT=$HOME/llvm-project/build/bin/llvm-lit -DCMAKE_BUILD_TYPE=Release)

echo "=== $(date +%T) gen configure+build"
rm -rf $W/ts-gen $W/ts-prof; mkdir -p $W/ts-prof
ABM_PHASE=gen cmake -S $TS -B $W/ts-gen "${common[@]}" -DTEST_SUITE_PROFILE_GENERATE=ON > $W/ts-gen.cmake.log 2>&1 || { echo cmake-gen-failed; tail $W/ts-gen.cmake.log; exit 1; }
ABM_PHASE=gen ninja -C $W/ts-gen -k 0 -j48 > $W/ts-gen.build.log 2>&1; echo "gen build rc=$? failed=$(grep -c '^FAILED' $W/ts-gen.build.log)"
echo "=== $(date +%T) run"
llvm-lit -j48 --timeout=900 -q $W/ts-gen > $W/ts-gen.lit.log 2>&1; echo "lit rc=$?"; tail -5 $W/ts-gen.lit.log
echo "profiles: $(find $W/ts-prof -name '*.profdata' | wc -l)"
echo "=== $(date +%T) use configure+build"
rm -rf $W/ts-use $W/pgo/ir-ts
ABM_PHASE=use ABM_IRDIR=$W/pgo/ir-ts cmake -S $TS -B $W/ts-use "${common[@]}" -DTEST_SUITE_PROFILE_USE=ON > $W/ts-use.cmake.log 2>&1 || { echo cmake-use-failed; exit 1; }
ABM_PHASE=use ABM_IRDIR=$W/pgo/ir-ts ninja -C $W/ts-use -k 0 -j48 > $W/ts-use.build.log 2>&1; echo "use build rc=$? failed=$(grep -c '^FAILED' $W/ts-use.build.log)"
echo "IR: $(ls $W/pgo/ir-ts | wc -l) benchmarks, $(find $W/pgo/ir-ts -name '*.ll' | wc -l) files, $(grep -l function_entry_count -r $W/pgo/ir-ts --include=*.ll | wc -l) with profile"
echo "=== $(date +%T) done"
