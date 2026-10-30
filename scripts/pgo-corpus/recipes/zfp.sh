#!/bin/bash
# zfp: CLI compress+decompress of a 192^3 float and double field at several rates/precisions/tolerances
fetch() { git clone -q --depth 1 https://github.com/LLNL/zfp.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_UTILITIES=ON -DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF \
    -DZFP_WITH_OPENMP=OFF -DZFP_WITH_CUDA=OFF
  ninja -C "$BLD/b" -j"$J" zfpcmd
  cp "$(find "$BLD/b" -type f -name zfp | head -1)" "$BLD/zfp"
}
workload() {
  mkdir -p "$BLD/tmp"; cd "$BLD/tmp"
  n="-3 192 192 192"
  i=0
  for m in "-r 8" "-r 16" "-p 20" "-a 1e-3" "-R"; do
    i=$((i+1))
    "$BLD/zfp" -f $n $m -i "$DATA/field192.f32" -z f$i.zfp -h
    "$BLD/zfp" -z f$i.zfp -o f$i.out -h
    "$BLD/zfp" -d $n $m -i "$DATA/field192.f64" -z d$i.zfp -h
    "$BLD/zfp" -z d$i.zfp -o d$i.out -h
  done
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
