#!/bin/bash
# bzip2: compress -9/-1 and decompress of 16 MB text
fetch() { git clone -q --depth 1 https://gitlab.com/bzip2/bzip2.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_SHARED_LIB=OFF -DENABLE_STATIC_LIB=ON -DENABLE_APP=ON -DENABLE_TESTS=OFF -DENABLE_DOCS=OFF
  ninja -C "$BLD/b" -j"$J" bzip2
  cp "$(find "$BLD/b" -type f -name bzip2 | head -1)" "$BLD/bzip2"
}
workload() {
  mkdir -p "$BLD/tmp"
  "$BLD/bzip2" -9 -c "$DATA/text16m" > "$BLD/tmp/a.bz2"
  "$BLD/bzip2" -d -c "$BLD/tmp/a.bz2" | cmp - "$DATA/text16m"
  "$BLD/bzip2" -1 -c "$DATA/text16m" > "$BLD/tmp/b.bz2"
  "$BLD/bzip2" -d -c "$BLD/tmp/b.bz2" > /dev/null
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
