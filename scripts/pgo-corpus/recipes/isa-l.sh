#!/bin/bash
# isa-l: noarch (C base) build; erasure_code/crc/igzip base tests and perf programs
fetch() { git clone -q --depth 1 https://github.com/intel/isa-l.git "$SRC"; }
build() {
  fresh_src
  cd "$SRC"
  M="make -f Makefile.unx arch=noarch CC=$CC AS=$CC -j$J"
  $M lib
  T="erasure_code_base_test gf_vect_mul_base_test gf_vect_dot_prod_base_test erasure_code_base_perf erasure_code_perf erasure_code_update_perf erasure_code_test gf_vect_dot_prod_perf gf_vect_mul_perf crc32_ieee_perf crc32_iscsi_perf crc16_t10dif_perf crc64_funcs_perf crc32_gzip_refl_perf crc_funcs_perf crc32_funcs_test crc64_funcs_test crc16_t10dif_test igzip_rand_test igzip_file_perf igzip_perf checksum32_funcs_test"
  $M $T
  mkdir -p "$BLD/bin"
  for f in $T; do cp "$f" "$BLD/bin/"; done
}
workload() {
  cd "$BLD/bin"
  for t in *; do echo "== $t"; case $t in igzip_file_perf|igzip_perf) timeout 600 ./$t "$DATA/text64m" || echo "rc=$? $t";; *) timeout 600 ./$t || echo "rc=$? $t";; esac; done
  true
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
