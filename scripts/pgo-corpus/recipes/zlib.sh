#!/bin/bash
# zlib: minigzip deflate at levels 1/6/9 and inflate on text
fetch() { git clone -q --depth 1 https://github.com/madler/zlib.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DZLIB_BUILD_TESTING=ON -DZLIB_BUILD_SHARED=OFF -DBUILD_SHARED_LIBS=OFF
  ninja -C "$BLD/b" -j"$J" minigzipstatic
  cp "$BLD/b/test/minigzipstatic" "$BLD/minigzip" 2>/dev/null || cp "$(find "$BLD/b" -name minigzipstatic -type f)" "$BLD/minigzip"
}
workload() {
  mkdir -p "$BLD/tmp"
  for l in 1 6 9; do
    "$BLD/minigzip" -$l < "$DATA/text64m" > "$BLD/tmp/a$l.gz"
    "$BLD/minigzip" -d < "$BLD/tmp/a$l.gz" | cmp - "$DATA/text64m"
  done
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
