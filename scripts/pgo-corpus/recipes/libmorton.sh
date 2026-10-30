#!/bin/bash
# libmorton: its test/benchmark program (2D/3D Morton encode/decode correctness + performance)
fetch() { git clone -q --depth 1 https://github.com/Forceflow/libmorton.git "$SRC"; }
build() {
  $CXX -I "$SRC/include" -c "$SRC/test/libmorton_test.cpp" -o "$BLD/libmorton_test.o"
  $CXX -o "$BLD/libmorton_test" "$BLD/libmorton_test.o"
}
workload() { cd "$BLD" && printf '\n' | ./libmorton_test 2>&1 | tail -50; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
