#!/bin/bash
# xz: LZMA2 compress -6 / -3 and decompress of text (CMake, no asm)
fetch() { git clone -q --depth 1 https://github.com/tukaani-project/xz.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
    -DBUILD_SHARED_LIBS=OFF -DXZ_NLS=OFF -DXZ_THREADS=no -DXZ_SANDBOX=no -DXZ_DOC=OFF -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release
  ninja -C "$BLD/b" -j"$J" xz
  cp "$BLD/b/xz" "$BLD/"
}
workload() {
  mkdir -p "$BLD/tmp"
  "$BLD/xz" -6 -T1 -k -c "$DATA/text64m" > "$BLD/tmp/a.xz"
  "$BLD/xz" -d -c "$BLD/tmp/a.xz" | cmp - "$DATA/text64m"
  "$BLD/xz" -3 -T1 -c "$DATA/text16m" > "$BLD/tmp/b.xz"
  "$BLD/xz" -d -c "$BLD/tmp/b.xz" > /dev/null
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
