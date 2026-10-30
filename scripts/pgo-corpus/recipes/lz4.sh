#!/bin/bash
# lz4: built-in benchmark levels 1-9 on 64 MB text
fetch() { git clone -q --depth 1 https://github.com/lz4/lz4.git "$SRC"; }
build() {
  fresh_src
  make -C "$SRC/programs" -j"$J" lz4 CC="$CC" CFLAGS=-DNDEBUG
  cp "$SRC/programs/lz4" "$BLD/"
}
workload() {
  "$BLD/lz4" -b1 -e9 -i1 "$DATA/text64m"
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
