#!/bin/bash
# cpython: the interpreter running Monte Carlo style code on random (MT19937) plus a shuffle/sample mix
fetch() { git clone -q --depth 1 --branch v3.13.1 https://github.com/python/cpython.git "$SRC"; }
build() {
  cd "$BLD"
  "$SRC/configure" CC="$CC" --disable-shared --without-ensurepip --without-doc-strings -q
  make -j"$J"
}
workload() {
  "$BLD/python" -c '
import random
r = random.random
inside = 0
for i in range(1500000):
    x = r(); y = r()
    inside += x*x + y*y < 1.0
print(4 * inside / 1500000)
a = list(range(10000))
for i in range(60):
    random.shuffle(a); random.sample(a, 500)
print(sum(random.getrandbits(64) & 1 for _ in range(300000)))
'
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
