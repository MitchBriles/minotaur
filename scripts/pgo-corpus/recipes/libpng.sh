#!/bin/bash
# libpng: pnm2png encode / png2pnm decode of a 3000x2000 photo-like PPM, plus pngtest loop
fetch() {
  git clone -q --depth 1 https://github.com/pnggroup/libpng.git "$SRC"
  git clone -q --depth 1 https://github.com/madler/zlib.git "$SRC.zlib"
}
build() {
  cmake -S "$SRC.zlib" -B "$BLD/z" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DZLIB_BUILD_EXAMPLES=OFF -DZLIB_BUILD_TESTING=OFF -DZLIB_BUILD_SHARED=OFF -DBUILD_SHARED_LIBS=OFF
  ninja -C "$BLD/z" -j"$J"
  zl=$(ls "$BLD"/z/libz*.a | head -1)
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DPNG_SHARED=OFF -DPNG_STATIC=ON -DPNG_TESTS=OFF -DPNG_TOOLS=OFF -DPNG_FRAMEWORK=OFF \
    -DPNG_HARDWARE_OPTIMIZATIONS=OFF -DZLIB_ROOT="$BLD/z" \
    -DZLIB_INCLUDE_DIR="$SRC.zlib" -DZLIB_LIBRARY="$zl"
  ninja -C "$BLD/b" -j"$J"
  png=$(ls "$BLD"/b/libpng1*.a | head -1)
  inc="-I$SRC -I$BLD/b -I$SRC.zlib -I$BLD/z"
  for t in png2pnm pnm2png; do
    "$CC" $inc "$SRC/contrib/pngminus/$t.c" "$png" "$zl" -lm -o "$BLD/$t"
  done
  "$CC" $inc "$SRC/pngtest.c" "$png" "$zl" -lm -o "$BLD/pngtest"
  cp "$SRC/pngtest.png" "$BLD/pngtest.png"
}
workload() {
  mkdir -p "$BLD/tmp"; cd "$BLD/tmp"
  "$BLD/pnm2png" "$DATA/photo.ppm" big.png
  "$BLD/png2pnm" big.png big.ppm
  cmp big.ppm "$DATA/photo.ppm"
  "$BLD/pnm2png" -i "$DATA/photo.ppm" bigi.png
  "$BLD/png2pnm" bigi.png /dev/null
  for i in $(seq 1 300); do "$BLD/pngtest" "$BLD/pngtest.png" >/dev/null; done
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
