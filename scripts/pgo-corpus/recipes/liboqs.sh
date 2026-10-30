#!/bin/bash
# liboqs: speed_kem and speed_sig, 1 s per op, over ML-KEM, ML-DSA, Falcon, SLH-DSA/SPHINCS+, HQC, McEliece-348864, FrodoKEM, etc, generic C
fetch() { git clone -q --depth 1 https://github.com/open-quantum-safe/liboqs.git "$SRC"; }
build() {
  cmake -S "$SRC" -B "$BLD" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
    -DOQS_DIST_BUILD=OFF -DOQS_OPT_TARGET=generic -DOQS_USE_OPENSSL=OFF -DBUILD_SHARED_LIBS=OFF \
    -DOQS_BUILD_ONLY_LIB=OFF -DOQS_MINIMAL_BUILD= -DOQS_ENABLE_KEM_CLASSIC_MCELIECE=ON \
    -DOQS_USE_AVX2_INSTRUCTIONS=OFF -DOQS_USE_AVX512_INSTRUCTIONS=OFF -DOQS_USE_BMI1_INSTRUCTIONS=OFF -DOQS_USE_BMI2_INSTRUCTIONS=OFF \
    -DOQS_USE_POPCNT_INSTRUCTIONS=OFF -DOQS_USE_AES_INSTRUCTIONS=OFF -DOQS_USE_SHA2_INSTRUCTIONS=OFF -DOQS_USE_SSE2_INSTRUCTIONS=OFF -DOQS_USE_SSE3_INSTRUCTIONS=OFF -DOQS_USE_ARM_NEON_INSTRUCTIONS=OFF
  cmake --build "$BLD" -j"$J" --target speed_kem speed_sig
}
workload() {
  cd "$BLD/tests"
  RE='ML-KEM|ML-DSA|Falcon|SPHINCS|SLH|HQC|Classic-McEliece-348864$|FrodoKEM-640|sntrup|BIKE-L1|CROSS-rsdp-128-balanced|MAYO-1|SNOVA_24_5_4$|OV-Is$'
  for t in speed_kem speed_sig; do
    for a in $(./$t --algs | grep -E "$RE" | grep -v -i 'f$' ); do
      echo "== $t $a"; timeout 300 ./$t -d 1 "$a" || echo "rc=$? $a"
    done
  done
  true
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
