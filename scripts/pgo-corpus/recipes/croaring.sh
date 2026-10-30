#!/bin/bash
# croaring: roaring_benchmark (unified, real-bitmap datasets), AVX/x64 optimisations disabled
fetch() { git clone -q --depth 1 https://github.com/RoaringBitmap/CRoaring.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
    -DROARING_DISABLE_AVX=ON -DROARING_DISABLE_AVX512=ON -DROARING_DISABLE_X64=ON -DROARING_DISABLE_NATIVE=ON \
    -DENABLE_ROARING_TESTS=ON -DENABLE_ROARING_BENCHMARKS=ON -DROARING_USE_CPM=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build "$BLD" -j"$J" --target roaring_benchmark
}
workload() { cd "$BLD/benchmarks" && ./roaring_benchmark; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
