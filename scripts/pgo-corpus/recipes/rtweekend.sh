#!/bin/bash
# rtweekend: "Ray Tracing in One Weekend" renderer; random_double() is std::mt19937 + uniform_real_distribution
fetch() { git clone -q --depth 1 https://github.com/RayTracing/raytracing.github.io.git "$SRC"; }
build() {
  fresh_src
  d="$SRC/src/InOneWeekend"
  sed -i 's/image_width *= *[0-9]*/image_width = 240/; s/samples_per_pixel *= *[0-9]*/samples_per_pixel = 24/; s/max_depth *= *[0-9]*/max_depth = 20/' "$d/main.cc"
  "$CXX" -std=c++17 -o "$BLD/rtweekend" "$d/main.cc"
}
workload() { "$BLD/rtweekend" > /dev/null; }
source "$HOME/abm-work/pgo/lib.sh"
pgo_main
