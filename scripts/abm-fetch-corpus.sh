#!/bin/bash
# Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
# Distributed under the MIT license that can be found in the LICENSE file.
#
# usage: abm-fetch-corpus.sh PROJECTS DIR
#
# Fetches bench/<project>/optimized from llvm-opt-benchmark for each project
# named in PROJECTS (one per line) into a sparse checkout at DIR. One project
# per request: asking for every blob at once stalls on GitHub's side, so run a
# few of these in parallel on separate DIRs instead. Rerunning skips projects
# already present; each is capped at 15 minutes.
set -u
LIST=$1 DIR=$2
[ -d "$DIR/.git" ] || git clone -q --filter=blob:none --no-checkout --depth 1 \
  https://github.com/dtcxzyw/llvm-opt-benchmark.git "$DIR" || exit 1
cd "$DIR" || exit 1
[ -f .git/info/sparse-checkout ] || {
  git sparse-checkout set --no-cone /README.md
  git checkout -q 2>/dev/null || true
}
while read -r p; do
  [ -d "bench/$p/optimized" ] && { echo "have $p"; continue; }
  start=$SECONDS
  if timeout -k 30 900 git sparse-checkout add "/bench/$p/optimized/"; then
    echo "ok $p $((SECONDS-start))s $(ls "bench/$p/optimized" 2>/dev/null | wc -l) files"
  else
    echo "FAIL $p rc=$? $((SECONDS-start))s"
  fi
done < "$LIST"
