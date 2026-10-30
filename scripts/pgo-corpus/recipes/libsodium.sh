#!/bin/bash
# libsodium: make check test programs (aead, hash, kdf/pwhash, sign, box, ...), no asm, static
fetch() { git clone -q --depth 1 https://github.com/jedisct1/libsodium.git "$SRC"; }
build() {
  fresh_src
  cd "$SRC"
  ./autogen.sh -s >/dev/null 2>&1 || ./autogen.sh
  ./configure CC="$CC" --disable-asm --enable-minimal=no --disable-shared --enable-static
  make -j"$J"
  make -C test -j"$J" check TESTS=
}
workload() { make -C "$SRC/test" check -j8; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
