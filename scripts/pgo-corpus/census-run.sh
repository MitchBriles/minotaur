#!/bin/bash
# census-run.sh LIST OUT [JOBS]: census (all nested regions) -> shortlist ->
# llc + alive-tv on the shortlist -> fixed-instruction ranking.
set -uo pipefail
LIST=$1 OUT=$2 J=${3:-56}
M=${MINOTAUR:-$HOME/minotaur}
export ALIVE_TV=${ALIVE_TV:-$HOME/alive2-abm/build/alive-tv} LLC=${LLC:-$HOME/llvm-project/build/bin/llc}
mkdir -p "$OUT"
echo "=== $(date +%T) census $(wc -l < "$LIST") files"
$M/build/abm-collect -c -j "$J" -m 2048 -t 600 -o "$OUT/census.jsonl" -f "$LIST" \
  -x '-minotaur-abm-census-all' > "$OUT/collect.log" 2>&1
echo "collect rc=$? records=$(wc -l < "$OUT/census.jsonl")"
echo "=== $(date +%T) shortlist"
python3 $M/scripts/abm-fixed.py --samples "$OUT/samples.jsonl" "$OUT/census.jsonl" > /dev/null 2> "$OUT/fixed0.log"
tail -2 "$OUT/fixed0.log"
echo "=== $(date +%T) llc"
python3 $M/scripts/abm-llc.py -j 60 --mem 2048 --memo "$OUT/llc.jsonl" "$OUT/samples.jsonl" 2>&1 | tail -1
echo "=== $(date +%T) proofs"
python3 $M/scripts/abm-verify.py -j 60 --mem 4096 --smt-to 20 --memo "$OUT/proof.jsonl" "$OUT/samples.jsonl" 2>&1 | tail -2
echo "=== $(date +%T) rank"
python3 $M/scripts/abm-fixed.py --llc "$OUT/llc.jsonl" --verified "$OUT/proof.jsonl" \
  --json "$OUT/fixed.json" --top 60 "$OUT/census.jsonl" > "$OUT/fixed.txt" 2> "$OUT/fixed1.log"
tail -2 "$OUT/fixed1.log"
echo "=== $(date +%T) done"
