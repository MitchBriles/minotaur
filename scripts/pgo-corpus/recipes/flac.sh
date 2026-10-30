#!/bin/bash
# flac: encode 60 s 44.1k stereo music-like WAV at -8 and -5, decode
fetch() { git clone -q --depth 1 https://github.com/xiph/flac.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_BUILD_TYPE=Release \
    -DWITH_OGG=OFF -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DBUILD_DOCS=OFF -DBUILD_EXAMPLES=OFF \
    -DINSTALL_MANPAGES=OFF -DWITH_ASM=OFF -DBUILD_PROGRAMS=ON
  ninja -C "$BLD/b" -j"$J" flac
  cp "$(find "$BLD/b" -type f -name flac | head -1)" "$BLD/flac"
}
workload() {
  mkdir -p "$BLD/tmp"; cd "$BLD/tmp"
  for l in 8 5; do
    "$BLD/flac" -$l -s -f -o m$l.flac "$DATA/music60.wav"
    "$BLD/flac" -d -s -f -o m$l.wav m$l.flac
    cmp m$l.wav "$DATA/music60.wav"
  done
  "$BLD/flac" -8 -p -e -s -f -o mp.flac "$DATA/music60.wav"
  "$BLD/flac" -t -s mp.flac
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
