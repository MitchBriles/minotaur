#!/bin/bash
# brotli: compress q5/q9 on 16 MB text, q11 on 2 MB, decompress
fetch() { git clone -q --depth 1 https://github.com/google/brotli.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DBROTLI_DISABLE_TESTS=ON
  ninja -C "$BLD/b" -j"$J" brotli
  cp "$BLD/b/brotli" "$BLD/"
}
workload() {
  mkdir -p "$BLD/tmp"
  head -c 2097152 "$DATA/text16m" > "$BLD/tmp/t2m"
  for q in 5 9; do
    "$BLD/brotli" -q $q -c "$DATA/text16m" > "$BLD/tmp/a$q.br"
    "$BLD/brotli" -d -c "$BLD/tmp/a$q.br" | cmp - "$DATA/text16m"
  done
  "$BLD/brotli" -q 11 -c "$BLD/tmp/t2m" > "$BLD/tmp/c.br"
  "$BLD/brotli" -d -c "$BLD/tmp/c.br" | cmp - "$BLD/tmp/t2m"
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
