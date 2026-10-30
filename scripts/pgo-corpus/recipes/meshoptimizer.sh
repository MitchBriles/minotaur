#!/bin/bash
# meshoptimizer (no SIMD): bundled demo (index/vertex codec, vcache, simplify, meshlets...) on pirate.obj, repeated
fetch() { git clone -q --depth 1 https://github.com/zeux/meshoptimizer.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD/b" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_BUILD_TYPE=Release \
    -DMESHOPT_BUILD_DEMO=ON -DMESHOPT_WERROR=OFF -DCMAKE_CXX_FLAGS=-DMESHOPTIMIZER_NO_SIMD
  ninja -C "$BLD/b" -j"$J" demo
  cp "$(find "$BLD/b" -type f -name meshoptdemo | head -1)" "$BLD/meshoptdemo"
  cp "$SRC/demo/pirate.obj" "$BLD/"
}
workload() {
  cd "$BLD"
  for i in 1 2 3; do ./meshoptdemo pirate.obj > /dev/null; done
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
