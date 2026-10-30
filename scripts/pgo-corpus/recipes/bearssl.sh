#!/bin/bash
# bearssl: testspeed over all algorithms (hashes, ciphers, GHASH, RSA, EC, ...)
fetch() { git clone -q --depth 1 https://www.bearssl.org/git/BearSSL "$SRC"; }
build() {
  fresh_src
  make -C "$SRC" -j"$J" CC="$CC" LD="$CC" CFLAGS="-W -Wall -fPIC" build/testspeed
  cp "$SRC/build/testspeed" "$BLD/"
}
workload() { "$BLD/testspeed" all; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
