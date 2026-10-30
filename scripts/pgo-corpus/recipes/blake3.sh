#!/bin/bash
# blake3: portable implementation hashing 64 MB text 20 times (c/example)
fetch() { git clone -q --depth 1 https://github.com/BLAKE3-team/BLAKE3.git "$SRC"; }
build() {
  cd "$SRC/c"
  D="-DBLAKE3_NO_SSE2 -DBLAKE3_NO_SSE41 -DBLAKE3_NO_AVX2 -DBLAKE3_NO_AVX512"
  for f in example blake3 blake3_dispatch blake3_portable; do $CC $D -c $f.c -o "$BLD/$f.o"; done
  $CC -o "$BLD/example" "$BLD"/*.o
}
workload() { for i in $(seq 20); do "$BLD/example" < "$DATA/text64m"; done; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
