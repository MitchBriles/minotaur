#!/bin/bash
# zstd: levels 1-19 compress + decompress of 64 MB of text
fetch() { git clone -q --depth 1 https://github.com/facebook/zstd.git "$SRC"; }
build() {
  fresh_src
  make -C "$SRC/programs" -j"$J" zstd CC="$CC" MOREFLAGS= ZSTD_NO_ASM=1 HAVE_THREAD=0
  cp "$SRC/programs/zstd" "$BLD/"
}
workload() {
  "$BLD/zstd" -q -b1e19 -i1 "$DATA/text64m"
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
