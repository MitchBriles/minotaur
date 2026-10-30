#!/bin/bash
# libdeflate (default cpu dispatch): gzip levels 1/6/12 compress+decompress of text, plus built-in benchmark
fetch() { git clone -q --depth 1 https://github.com/ebiggers/libdeflate.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DLIBDEFLATE_BUILD_SHARED_LIB=OFF -DLIBDEFLATE_BUILD_GZIP=ON -DLIBDEFLATE_BUILD_TESTS=ON
  ninja -C "$BLD/b" -j"$J" libdeflate-gzip benchmark
  cp "$(find "$BLD/b" -type f -name libdeflate-gzip | head -1)" "$BLD/libdeflate-gzip"
  cp "$(find "$BLD/b" -type f -name benchmark | head -1)" "$BLD/benchmark"
}
workload() {
  mkdir -p "$BLD/tmp"
  for l in 1 6 12; do
    "$BLD/libdeflate-gzip" -$l -c "$DATA/text64m" > "$BLD/tmp/a$l.gz"
    "$BLD/libdeflate-gzip" -d -c "$BLD/tmp/a$l.gz" | cmp - "$DATA/text64m"
  done
  "$BLD/benchmark" -6 "$DATA/text16m"
  "$BLD/benchmark" -g -3 "$DATA/text16m"
  "$BLD/benchmark" -z -9 "$DATA/text16m"
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
