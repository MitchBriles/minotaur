#!/bin/bash
# x264 (no asm): encode 150-frame CIF generated clip with --preset medium and veryfast
fetch() { git clone -q --depth 1 https://code.videolan.org/videolan/x264.git "$SRC"; }
build() {
  fresh_src
  cd "$SRC"
  ./configure --disable-asm --enable-static --disable-opencl --disable-lavf --disable-swscale \
    --disable-ffms --disable-gpac --disable-lsmash --disable-avs CC="$CC"
  make -j"$J" x264
  cp "$SRC/x264" "$BLD/"
}
workload() {
  mkdir -p "$BLD/tmp"; cd "$BLD/tmp"
  "$BLD/x264" --preset medium --threads 1 --input-res 352x288 --fps 25 --crf 23 -o a.264 "$DATA/clip_cif.yuv"
  "$BLD/x264" --preset veryfast --threads 1 --input-res 352x288 --fps 25 --bitrate 800 -o b.264 "$DATA/clip_cif.yuv"
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
