#!/bin/bash
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
#
# usage: abm-ext-corpus.sh [ROOT]   (default ./corpus-ext; CLANG=... to pick one)
#
# Fetches bit-manipulation-heavy projects that llvm-opt-benchmark lacks and
# compiles every C file to scalar LLVM IR. No project build scripts run: each
# .c is compiled on its own, and files that need generated headers just fail.
set -u
ROOT=${1:-corpus-ext}
SRC=$ROOT/src
OUT=$ROOT/bench
CLANG=${CLANG:-clang}
mkdir -p "$SRC" "$OUT"

REPOS=(
  "isa-l https://github.com/intel/isa-l"
  "gf-complete https://github.com/ceph/gf-complete"
  "jerasure https://github.com/tsuraan/Jerasure"
  "libcorrect https://github.com/quiet/libcorrect"
  "pqclean https://github.com/PQClean/PQClean"
  "bearssl https://www.bearssl.org/git/BearSSL"
  "blake3 https://github.com/BLAKE3-team/BLAKE3"
  "monocypher https://github.com/LoupVaillant/Monocypher"
  "xxhash https://github.com/Cyan4973/xxHash"
  "croaring https://github.com/RoaringBitmap/CRoaring"
  "capstone https://github.com/capstone-engine/capstone"
  "softfloat https://github.com/ucb-bar/berkeley-softfloat-3"
  "mibench https://github.com/embecosm/mibench"
  "hackers-delight https://github.com/hcs0/Hackers-Delight"
)

compile_one() {
  local proj=$1 src=$2 rel=$3 incs=$4
  local out="$OUT/$proj/scalar/${rel%.c}.ll"
  mkdir -p "$(dirname "$out")"
  # shellcheck disable=SC2086
  if timeout -k 5 120 "$CLANG" -O2 -fno-vectorize -fno-slp-vectorize -w \
       -std=gnu11 -S -emit-llvm $incs -DNDEBUG -o "$out.tmp" "$src" \
       2>/dev/null; then
    # name the file as llvm-opt-benchmark does, so reports group by project
    sed -i "s|^source_filename = .*|source_filename = \"bench/$proj/scalar/$rel\"|" \
      "$out.tmp" && mv "$out.tmp" "$out" && echo ok || echo fail
  else
    rm -f "$out.tmp"
    echo fail
  fi
}
export -f compile_one
export CLANG OUT

for entry in "${REPOS[@]}"; do
  set -- $entry
  proj=$1 url=$2
  dir=$SRC/$proj
  if [ ! -d "$dir" ]; then
    timeout -k 10 600 git clone -q --depth 1 "$url" "$dir" 2>/dev/null ||
      { echo "FETCH-FAIL $proj"; rm -rf "$dir"; continue; }
  fi
  # every directory holding a header, so most files find their includes
  incs=$(find "$dir" -name '*.h' -printf '%h\n' | sort -u | head -300 |
         sed 's|^|-I|' | tr '\n' ' ')
  n=$(find "$dir" -name '*.c' \
        -not -path '*/test*' -not -path '*/bench*' -not -path '*/example*' |
      while read -r f; do
        compile_one "$proj" "$f" "${f#$dir/}" "$incs"
      done | sort | uniq -c | tr '\n' ' ')
  echo "$proj: $n"
done
echo "=== done"
