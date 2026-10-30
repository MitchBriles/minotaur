#!/bin/bash
# softfloat: Berkeley SoftFloat-3 + TestFloat-3 testsoftfloat at level 1 over all unary and binary operations
TF=$HOME/abm-work/pgo/src/softfloat-testfloat
fetch() {
  git clone -q --depth 1 https://github.com/ucb-bar/berkeley-softfloat-3.git "$SRC"
  git clone -q --depth 1 https://github.com/ucb-bar/berkeley-testfloat-3.git "$TF"
}
build() {
  fresh_src
  [ -d "$TF" ] || git clone -q --depth 1 https://github.com/ucb-bar/berkeley-testfloat-3.git "$TF"
  git -C "$TF" clean -fdxq; git -C "$TF" checkout -q -- .
  cd "$SRC/build/Linux-x86_64-GCC"
  sed -i "s|^  gcc -c|  $CC -c|" Makefile
  make -j"$J"
  cd "$TF/build/Linux-x86_64-GCC"
  sed -i "s|^  gcc -c|  $CC -c|; s|^LINK = gcc|LINK = $CC|" Makefile
  make -j"$J" SOFTFLOAT_DIR="$SRC" testsoftfloat testfloat_gen
  cp testsoftfloat testfloat_gen "$BLD/"
}
workload() {
  cd "$BLD"
  ./testsoftfloat -level 1 -tininessbefore -all1 
  ./testsoftfloat -level 1 -tininessafter -all2
  ./testsoftfloat -level 1 -rnear_even -all1
  ./testsoftfloat -level 1 -rminMag -all2
  ./testsoftfloat -level 1 -rmax -all2
  ./testfloat_gen -level 1 f32_add | head -c 1000000 >/dev/null
  true
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
