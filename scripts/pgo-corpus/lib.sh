# Shared harness for profiled corpus recipes. A recipe defines fetch, build
# and workload, sources this file, then calls pgo_main. See README.txt.
set -euo pipefail
P=$(basename "$0" .sh)
ROOT=$HOME/abm-work/pgo
SRC=$ROOT/src/$P BLD=$ROOT/build/$P DATA=$ROOT/data PROF=$ROOT/prof/$P
LOGS=$ROOT/logs
export PATH=$HOME/llvm-project/build/bin:$PATH
export CC=$HOME/minotaur/scripts/abm-pgo-cc CXX=$HOME/minotaur/scripts/abm-pgo-c++
export ABM_CLANG=$HOME/llvm-project/build/bin/clang
export ABM_CLANGXX=$HOME/llvm-project/build/bin/clang++
export ABM_PROJECT=$P
J=${J:-24}
mkdir -p "$ROOT/src" "$ROOT/build" "$DATA" "$ROOT/prof" "$ROOT/ir" "$LOGS"

# for in-tree builds: back to a pristine checkout between the two phases
fresh_src() { git -C "$SRC" clean -fdxq; git -C "$SRC" checkout -q -- .; }

pgo_main() {
  local t0=$SECONDS
  [ -d "$SRC" ] || { echo "== $P fetch"; fetch; }
  echo "== $P gen build"
  rm -rf "$BLD" "$PROF"; mkdir -p "$BLD" "$PROF"
  export ABM_PHASE=gen
  build > "$LOGS/$P.gen.log" 2>&1 || { echo "FAIL $P gen build (see $LOGS/$P.gen.log)"; exit 1; }
  echo "== $P workload"
  export LLVM_PROFILE_FILE="$PROF/%m-%p.profraw"
  timeout -k 30 ${WORKLOAD_TIMEOUT:-1800} bash -c "$(declare -f workload); $(declare -p SRC BLD DATA P J); workload" \
    > "$LOGS/$P.run.log" 2>&1 || { echo "FAIL $P workload rc=$? (see $LOGS/$P.run.log)"; exit 1; }
  unset LLVM_PROFILE_FILE
  ls "$PROF"/*.profraw >/dev/null 2>&1 || { echo "FAIL $P no profiles"; exit 1; }
  llvm-profdata merge -o "$PROF/merged.profdata" "$PROF"/*.profraw
  rm -f "$PROF"/*.profraw
  echo "== $P use build"
  rm -rf "$BLD" "$ROOT/ir/$P"; mkdir -p "$BLD"
  export ABM_PHASE=use ABM_PROFDATA="$PROF/merged.profdata" ABM_IRDIR="$ROOT/ir"
  build > "$LOGS/$P.use.log" 2>&1 || { echo "FAIL $P use build (see $LOGS/$P.use.log)"; exit 1; }
  local n hot
  n=$(find "$ROOT/ir/$P" -name '*.ll' 2>/dev/null | wc -l)
  hot=$(find "$ROOT/ir/$P" -name '*.ll' -exec grep -l function_entry_count {} + 2>/dev/null | wc -l)
  echo "DONE $P ir_files=$n with_profile=$hot secs=$((SECONDS - t0))"
}
