#!/bin/bash
# xxhash: xxhsum -b benchmark of all algorithms, scalar (XXH_VECTOR=0), plus hashing 64 MB text
fetch() { git clone -q --depth 1 https://github.com/Cyan4973/xxHash.git "$SRC"; }
build() {
  fresh_src
  make -C "$SRC" -j"$J" xxhsum CC="$CC" DISPATCH=0 CPPFLAGS="-DXXH_VECTOR=0" CFLAGS=
  cp "$SRC/xxhsum" "$BLD/"
}
workload() {
  "$BLD/xxhsum" -b -i3
  for a in 0 1 2 3; do "$BLD/xxhsum" -H$a "$DATA/text64m"; done
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
