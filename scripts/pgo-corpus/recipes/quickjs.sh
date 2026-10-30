#!/bin/bash
# quickjs: qjs running tests/microbench.js plus the language/builtin/bigint test scripts
fetch() { git clone -q --depth 1 https://github.com/bellard/quickjs.git "$SRC"; }
build() {
  fresh_src
  make -C "$SRC" -j"$J" qjs CC="$CC" HOST_CC="$CC" CROSS_PREFIX= CONFIG_LTO= 
  cp "$SRC/qjs" "$BLD/" 2>/dev/null || cp "$SRC"/build/qjs "$BLD/"
}
workload() {
  cd "$SRC/tests"
  "$BLD/qjs" microbench.js
  for t in test_language test_builtin test_bigint test_closure test_loop; do "$BLD/qjs" $t.js; done
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
