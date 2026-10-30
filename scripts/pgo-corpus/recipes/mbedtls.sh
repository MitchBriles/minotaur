#!/bin/bash
# mbedtls: programs/test/benchmark (hashes, ciphers, AEAD, RSA, ECDSA, ECDH, ...), no AESNI/asm
fetch() { git clone -q --depth 1 --recurse-submodules --shallow-submodules https://github.com/Mbed-TLS/mbedtls.git "$SRC"; }
build() {
  fresh_src
  git -C "$SRC" submodule foreach -q --recursive 'git clean -fdxq; git checkout -q -- .'
  cd "$SRC"
  CFG=tf-psa-crypto/scripts/config.py; [ -f $CFG ] || CFG=scripts/config.py
  for o in MBEDTLS_AESNI_C MBEDTLS_AESCE_C MBEDTLS_HAVE_ASM MBEDTLS_PADLOCK_C; do python3 $CFG -f tf-psa-crypto/include/psa/crypto_config.h unset $o || python3 $CFG unset $o || true; done
  cmake -S "$SRC" -B "$BLD" -G Ninja -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
    -DENABLE_TESTING=OFF -DENABLE_PROGRAMS=ON -DUSE_SHARED_MBEDTLS_LIBRARY=OFF -DUSE_STATIC_MBEDTLS_LIBRARY=ON
  cmake --build "$BLD" -j"$J" 
}
workload() { B=$(find "$BLD" -name benchmark -type f -perm -u+x | head -1); "$B"; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
