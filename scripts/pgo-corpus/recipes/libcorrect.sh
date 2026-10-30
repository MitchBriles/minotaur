#!/bin/bash
# libcorrect: convolutional (viterbi) and Reed-Solomon test runners (encode/noise/decode loops), no SSE
fetch() { git clone -q --depth 1 https://github.com/quiet/libcorrect.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$BLD" -j"$J" --target convolutional_test_runner reed_solomon_test_runner
}
workload() { cd "$BLD/tests" && ./convolutional_test_runner && ./reed_solomon_test_runner; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
