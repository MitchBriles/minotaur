// Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
// Distributed under the MIT license that can be found in the LICENSE file.
#pragma once

#include "llvm/ADT/APInt.h"
#include "llvm/ADT/StringRef.h"

#include <vector>

namespace llvm {
class Function;
class raw_ostream;
class LoopInfo;
class BlockFrequencyInfo;
}

namespace minotaur {

// One `call @ABM(x, M, b)` with a constant matrix. The mask may be a runtime
// value, in which case maskIsConst is false and mask is meaningless.
struct ABMUse {
  unsigned width;
  llvm::APInt matrix;
  llvm::APInt mask;
  bool maskIsConst;
  bool exclusive;      // true for @ABMX (XOR semiring), false for @ABM (OR)
  unsigned loop_depth; // loop nesting of the containing block
  double rel_freq;     // block frequency relative to function entry; 1.0 when
                       // no BlockFrequencyInfo was available
  // What the compiler already knows about x at this site. A matrix only has to
  // agree with the original on reachable inputs, so without this a rewrite can
  // look far more exotic than the operation really is: the deflateInit2_ smear
  // is just x-1 on the powers of two that actually reach it.
  llvm::APInt known_zero;
  llvm::APInt known_one;
};

// Collects every ABM call in F whose matrix operand is a constant. LI and BFI
// are optional; when supplied they fill in loop_depth and rel_freq, which let
// the ranking weight a site by how often it is expected to run rather than
// counting every site equally.
void collectABMUses(const llvm::Function &F, std::vector<ABMUse> &out,
                    const llvm::LoopInfo *LI = nullptr,
                    const llvm::BlockFrequencyInfo *BFI = nullptr);

// Appends one JSON object per use to config::abm_log_path; a no-op when that
// path is unset. Lines are written whole so concurrent opt processes can share
// a log file. Negative costs are emitted as null.
// JSON string literal with escaping, shared with the census writer.
void writeJSONString(llvm::raw_ostream &os, llvm::StringRef s);

void writeABMRecords(const std::vector<ABMUse> &uses, llvm::StringRef kind,
                     llvm::StringRef srcIR, int costBefore, int costAfter,
                     llvm::StringRef evidence = "");

} // namespace minotaur
