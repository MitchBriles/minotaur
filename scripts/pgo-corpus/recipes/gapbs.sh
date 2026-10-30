#!/bin/bash
# gapbs: GAP benchmark suite; Kronecker/uniform graph generation (std::mt19937) + BFS/PR/CC kernels
fetch() { git clone -q --depth 1 https://github.com/sbeamer/gapbs.git "$SRC"; }
build() {
  fresh_src
  make -C "$SRC" -j"$J" CXX="$CXX" SERIAL=1 bfs pr cc tc
  cp "$SRC"/{bfs,pr,cc,tc} "$BLD/"
}
workload() {
  "$BLD/bfs" -g 18 -n 4
  "$BLD/pr" -u 18 -n 2
  "$BLD/cc" -g 17 -n 2
  "$BLD/tc" -g 14 -n 1
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
