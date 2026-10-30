#!/bin/bash
# mini-rv32ima: boot prebuilt Linux 6.1.14 rv32nommu image for 1.5e9 emulated instructions, then exit
fetch() {
  git clone -q --depth 1 https://github.com/cnlohr/mini-rv32ima.git "$SRC"
}
getimg() {
  mkdir -p "$DATA/rv32"; cd "$DATA/rv32"
  [ -f Image ] && return 0
  curl -fsSL -o linux.zip https://github.com/cnlohr/mini-rv32ima-images/raw/master/images/linux-6.1.14-rv32nommu-cnl-1.zip
  python3 -c "import zipfile;zipfile.ZipFile('linux.zip').extractall('.')"
}
build() {
  fresh_src
  (getimg)
  $CC -c "$SRC/mini-rv32ima/mini-rv32ima.c" -o "$BLD/mini-rv32ima.o"
  $CC -o "$BLD/mini-rv32ima" "$BLD/mini-rv32ima.o"
}
workload() {
  ls "$DATA/rv32"
  exec 3< <(sleep 900)
  "$BLD/mini-rv32ima" -f "$DATA/rv32/Image" -m 0x4000000 -k "console=ttyS0" -l -p -c 1500000000 <&3
}
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
