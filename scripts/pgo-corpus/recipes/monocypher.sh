#!/bin/bash
# monocypher: its tests/speed benchmark (chacha20, poly1305, blake2b, argon2, x25519, ed25519, sha512...)
fetch() { git clone -q --depth 1 https://github.com/LoupVaillant/Monocypher.git "$SRC"; }
build() {
  cd "$SRC"
  $CC -c src/monocypher.c -I src -o "$BLD/monocypher.o"
  $CC -c src/optional/monocypher-ed25519.c -I src -I src/optional -o "$BLD/ed.o"
  $CC -c tests/utils.c -I src -I tests -o "$BLD/utils.o"
  $CC -c tests/speed/speed.c -I src -I src/optional -I tests -I tests/speed -o "$BLD/speed.o"
  $CC -o "$BLD/speed" "$BLD"/*.o
}
workload() { "$BLD/speed"; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
