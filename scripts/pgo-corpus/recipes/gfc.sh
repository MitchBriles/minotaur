#!/bin/bash
# gfc: gf-complete gf_time (w=8,16,32, several methods) + Jerasure encoder/decoder on a 64 MB file, no SIMD
JER=$HOME/abm-work/pgo/src/gfc-jerasure
fetch() {
  git clone -q --depth 1 https://github.com/ceph/gf-complete.git "$SRC"
  git clone -q --depth 1 https://github.com/tsuraan/Jerasure.git "$JER"
}
build() {
  fresh_src
  [ -d "$JER" ] || git clone -q --depth 1 https://github.com/tsuraan/Jerasure.git "$JER"
  git -C "$JER" clean -fdxq; git -C "$JER" checkout -q -- .
  cd "$SRC"; ./autogen.sh
  ./configure CC="$CC" --prefix="$BLD/inst" --disable-shared --enable-static --disable-sse --disable-avx
  make -j"$J"; make install
  cd "$JER"; autoreconf -fi
  ./configure CC="$CC" --prefix="$BLD/inst" --disable-shared --enable-static CPPFLAGS="-I$BLD/inst/include" LDFLAGS="-L$BLD/inst/lib"
  make -j"$J"
  make -C Examples -j"$J" encoder decoder
  cp Examples/encoder Examples/decoder "$BLD/" 2>/dev/null || cp Examples/.libs/encoder Examples/.libs/decoder "$BLD/"
  cp "$SRC/tools/gf_time" "$BLD/" 2>/dev/null || cp "$SRC/tools/.libs/gf_time" "$BLD/"
  cp "$SRC/tools/gf_methods" "$BLD/" 2>/dev/null || true
}
workload() {
  cd "$BLD"
  S=1000000; N=30
  for w in 8 16 32; do
    ./gf_time $w A 1 $S $N -m TABLE - || true
    ./gf_time $w A 1 $S $N -m SHIFT - || true
    ./gf_time $w A 1 $S $N -m BYTWO_p - || true
    ./gf_time $w A 1 $S $N -m BYTWO_b - || true
    ./gf_time $w A 1 $S $N -m LOG - || true
    ./gf_time $w A 1 $S $N -m LOG_ZERO - || true
    ./gf_time $w A 1 $S $N -m CARRY_FREE - || true
    ./gf_time $w A 1 $S $N -m GROUP 4 4 - || true
  done
  ./gf_time 8 A 1 $S $N -m SPLIT 8 4 - || true
  ./gf_time 16 A 1 $S $N -m SPLIT 16 4 - || true
  ./gf_time 16 A 1 $S $N -m SPLIT 16 8 - || true
  ./gf_time 32 A 1 $S $N -m SPLIT 32 4 - || true
  ./gf_time 32 A 1 $S $N -m SPLIT 32 8 - || true
  ./gf_time 32 A 1 $S $N -m COMPOSITE 2 - - || true
  ./gf_time 16 A 1 $S $N -m COMPOSITE 2 - - || true
  mkdir -p work; cd work; cp "$DATA/text64m" f.txt
  run() { rm -rf Coding; ./../encoder f.txt "$@" >/dev/null; rm -f Coding/f_k01.txt Coding/f_m1.txt; ./../decoder f.txt >/dev/null; cmp Coding/f_decoded.txt f.txt; }
  run 10 6 reed_sol_van 8 0 0
  run 10 6 reed_sol_van 16 0 0
  run 8 4 cauchy_good 8 1024 0
  run 8 4 cauchy_orig 4 1024 0
  run 7 2 liberation 7 1024 0
  run 7 2 blaum_roth 6 1024 0
  run 10 2 liber8tion 8 1024 0
  run 10 2 reed_sol_r6_op 8 0 0
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
