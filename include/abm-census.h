// Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
// Distributed under the MIT license that can be found in the LICENSE file.
#pragma once

#include <chrono>
#include <optional>

namespace llvm {
class BlockFrequencyInfo;
class DominatorTree;
class Function;
class LoopInfo;
}

namespace minotaur {

// The census asks which affine bit manipulations a program performs, without
// asking the solver to search for anything. For every integer value it grows
// the largest pure expression DAG feeding it, evaluates that DAG concretely,
// recovers M and b from basis vectors over the concatenation of the DAG's
// inputs, and screens the result on random inputs. Nothing is rewritten.
// Records go to config::abm_log_path as kind "census" (affine) or "table"
// (not affine, but few enough reachable input bits to be a lookup table).
struct CensusOptions {
  unsigned max_depth = 12;   // operand edges from the root
  unsigned max_size = 64;    // instructions in a region
  unsigned max_in_bits = 128;
  unsigned max_out_bits = 64;
  unsigned min_size = 2;     // a single instruction is already an instruction
  unsigned table_bits = 8;   // log non-affine regions with at most this many
                             // unknown input bits as truth tables; 0 disables
  bool log_subsumed = false; // also log regions inside a larger logged one
  std::optional<std::chrono::steady_clock::time_point> deadline;
};

// Returns the number of records written.
unsigned runABMCensus(llvm::Function &F, const llvm::LoopInfo &LI,
                      const llvm::DominatorTree &DT,
                      const llvm::BlockFrequencyInfo *BFI,
                      const CensusOptions &O);

} // namespace minotaur
