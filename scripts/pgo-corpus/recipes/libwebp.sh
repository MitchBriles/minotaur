#!/bin/bash
# libwebp (no SIMD): cwebp lossy q75 / lossless on a 3000x2000 PPM, dwebp decode
fetch() { git clone -q --depth 1 https://github.com/webmproject/libwebp.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DWEBP_ENABLE_SIMD=OFF -DBUILD_SHARED_LIBS=OFF -DWEBP_BUILD_CWEBP=ON -DWEBP_BUILD_DWEBP=ON \
    -DWEBP_BUILD_ANIM_UTILS=OFF -DWEBP_BUILD_GIF2WEBP=OFF -DWEBP_BUILD_IMG2WEBP=OFF \
    -DWEBP_BUILD_VWEBP=OFF -DWEBP_BUILD_WEBPINFO=OFF -DWEBP_BUILD_WEBPMUX=OFF -DWEBP_BUILD_EXTRAS=OFF
  ninja -C "$BLD/b" -j"$J" cwebp dwebp
  cp "$BLD/b/cwebp" "$BLD/b/dwebp" "$BLD/"
}
workload() {
  mkdir -p "$BLD/tmp"; cd "$BLD/tmp"
  "$BLD/cwebp" -q 75 -m 4 -quiet "$DATA/photo.ppm" -o a.webp
  "$BLD/dwebp" -quiet -ppm a.webp -o a.ppm
  "$BLD/cwebp" -q 90 -m 6 -sns 80 -f 40 -segments 4 -quiet "$DATA/photo.ppm" -o b.webp
  "$BLD/dwebp" -quiet -nofancy -ppm b.webp -o b.ppm
  "$BLD/cwebp" -lossless -z 4 -quiet "$DATA/photo.ppm" -o c.webp
  "$BLD/dwebp" -quiet -ppm c.webp -o c.ppm
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
