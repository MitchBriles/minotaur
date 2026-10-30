#!/bin/bash
# libjpeg-turbo (no SIMD): cjpeg/djpeg on a 3000x2000 PPM at several settings, plus tjbench
fetch() { git clone -q --depth 1 https://github.com/libjpeg-turbo/libjpeg-turbo.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DWITH_SIMD=0 -DENABLE_SHARED=0 -DENABLE_STATIC=1 -DWITH_TURBOJPEG=1
  ninja -C "$BLD/b" -j"$J"
  for t in cjpeg djpeg tjbench; do
    f=$(find "$BLD/b" -maxdepth 1 -type f \( -name "$t" -o -name "$t-static" \) | head -1)
    cp "$f" "$BLD/$t"
  done
}
workload() {
  mkdir -p "$BLD/tmp"; cd "$BLD/tmp"
  for q in 50 75 95; do
    "$BLD/cjpeg" -quality $q -outfile a$q.jpg "$DATA/photo.ppm"
    "$BLD/djpeg" -outfile /dev/null a$q.jpg
  done
  "$BLD/cjpeg" -quality 85 -optimize -progressive -outfile p.jpg "$DATA/photo.ppm"
  "$BLD/djpeg" -dct float -outfile /dev/null p.jpg
  "$BLD/djpeg" -nosmooth -outfile /dev/null p.jpg
  "$BLD/tjbench" "$DATA/photo.ppm" 85 -nowrite -benchtime 5 -warmup 1
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
