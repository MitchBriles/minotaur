#!/bin/bash
# stockfish: ./stockfish bench (default positions, depth 14), generic x86-64 NNUE
fetch() { git clone -q --depth 1 https://github.com/official-stockfish/Stockfish.git "$SRC"; }
build() {
  fresh_src
  cd "$SRC/src"
  make -j"$J" build COMP=clang CXX="$CXX" ARCH=x86-64
  cp stockfish "$BLD/"
}
workload() { cd "$SRC/src" && "$BLD/stockfish" bench 16 1 14; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
