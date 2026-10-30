#!/bin/bash
# opus (no intrinsics): opus_demo encode/decode of 30 s 48k stereo music at 24/64/128 kbps
fetch() {
  mkdir -p "$SRC"
  curl -fsSL https://github.com/xiph/opus/releases/download/v1.5.2/opus-1.5.2.tar.gz | tar xz -C "$SRC" --strip-components=1
}
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DOPUS_DISABLE_INTRINSICS=ON -DOPUS_BUILD_PROGRAMS=ON \
    -DOPUS_BUILD_TESTING=OFF -DOPUS_INSTALL_PKG_CONFIG_MODULE=OFF -DOPUS_INSTALL_CMAKE_CONFIG_MODULE=OFF
  ninja -C "$BLD/b" -j"$J" opus_demo
  cp "$BLD/b/opus_demo" "$BLD/"
}
workload() {
  mkdir -p "$BLD/tmp"; cd "$BLD/tmp"
  for br in 24000 64000 128000; do
    "$BLD/opus_demo" -e audio 48000 2 $br -complexity 10 "$DATA/music30_48k.pcm" e$br.bit
    "$BLD/opus_demo" -d 48000 2 e$br.bit d$br.pcm
  done
  "$BLD/opus_demo" -e voip 48000 2 16000 -complexity 5 -cvbr "$DATA/music30_48k.pcm" v.bit
  "$BLD/opus_demo" -d 48000 2 v.bit v.pcm
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
