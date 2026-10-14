// Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
// Distributed under the MIT license that can be found in the LICENSE file.
#include "abm-log.h"
#include "config.h"

#include "llvm/ADT/SmallString.h"
#include "llvm/Analysis/BlockFrequencyInfo.h"
#include "llvm/Analysis/LoopInfo.h"
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/Support/KnownBits.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/Instructions.h"
#include "llvm/Support/Casting.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/Format.h"
#include "llvm/Support/raw_ostream.h"

#include <string>

using namespace llvm;
using namespace std;

namespace minotaur {

void collectABMUses(const Function &F, vector<ABMUse> &out, const LoopInfo *LI,
                    const BlockFrequencyInfo *BFI) {
  double entry = 0.0;
  if (BFI)
    entry = (double)BFI->getEntryFreq().getFrequency();

  for (auto &BB : F) {
    unsigned depth = LI ? LI->getLoopDepth(&BB) : 0;
    double freq = 1.0;
    if (BFI && entry > 0.0)
      freq = (double)BFI->getBlockFreq(&BB).getFrequency() / entry;

    for (auto &I : BB) {
      auto *CI = dyn_cast<CallInst>(&I);
      if (!CI)
        continue;
      auto *Callee = CI->getCalledFunction();
      if (!Callee)
        continue;
      auto name = Callee->getName();
      // @ABM sums with OR, @ABMX with XOR
      bool exclusive = name == "ABMX";
      if (name != "ABM" && !exclusive)
        continue;
      if (CI->arg_size() != 3)
        continue;
      auto *M = dyn_cast<ConstantInt>(CI->getArgOperand(1));
      auto *MaskTy = CI->getArgOperand(2)->getType();
      if (!M || !MaskTy->isIntegerTy())
        continue;
      // the mask may be a runtime value; the matrix is what gets ranked
      auto *B = dyn_cast<ConstantInt>(CI->getArgOperand(2));
      unsigned width = MaskTy->getIntegerBitWidth();
      KnownBits Known(width);
      if (auto *FParent = F.getParent())
        Known = computeKnownBits(CI->getArgOperand(0), FParent->getDataLayout());
      out.push_back({width, M->getValue(),
                     B ? B->getValue() : APInt(width, 0), B != nullptr,
                     exclusive, depth, freq, Known.Zero, Known.One});
    }
  }
}

void writeJSONString(raw_ostream &os, StringRef s) {
  static const char hex[] = "0123456789abcdef";
  os << '"';
  for (char c : s) {
    switch (c) {
    case '"':  os << "\\\""; break;
    case '\\': os << "\\\\"; break;
    case '\n': os << "\\n";  break;
    case '\r': os << "\\r";  break;
    case '\t': os << "\\t";  break;
    default: {
      unsigned char u = (unsigned char)c;
      if (u < 0x20)
        os << "\\u00" << hex[u >> 4] << hex[u & 0xf];
      else
        os << c;
    }
    }
  }
  os << '"';
}

// The replaced function is only ever read for provenance, and on real code it
// runs to tens of KB -- and repeats for every ABM in the same function. Keep a
// bounded prefix instead; it still identifies the function and its first
// instructions.
static constexpr size_t max_src_bytes = 2048;

static void writeJSONSrc(raw_ostream &os, StringRef s) {
  if (s.size() <= max_src_bytes) {
    writeJSONString(os, s);
    return;
  }
  // don't cut a line in half
  StringRef head = s.substr(0, max_src_bytes);
  size_t nl = head.rfind('\n');
  if (nl != StringRef::npos)
    head = head.substr(0, nl + 1);
  SmallString<2200> buf(head);
  buf += "; ... truncated, ";
  buf += to_string(s.size() - head.size());
  buf += " bytes elided\n";
  writeJSONString(os, buf);
}

static string toHex(const APInt &V) {
  SmallString<128> S;
  V.toStringUnsigned(S, 16);
  return string(S);
}

void writeABMRecords(const vector<ABMUse> &uses, StringRef kind, StringRef srcIR,
                     int costBefore, int costAfter, StringRef evidence) {
  if (config::abm_log_path.empty() || uses.empty())
    return;

  error_code EC;
  raw_fd_ostream os(config::abm_log_path, EC,
                    sys::fs::OF_Append | sys::fs::OF_Text);
  if (EC) {
    config::dbg() << "[abm-log] cannot open " << config::abm_log_path << ": "
                  << EC.message() << "\n";
    return;
  }
  // unbuffered: a record longer than the buffer would otherwise go out in
  // several writes and interleave with other processes appending to the log
  os.SetUnbuffered();

  for (auto &U : uses) {
    string line;
    raw_string_ostream ls(line);
    ls << "{\"kind\":";
    writeJSONString(ls, kind);
    ls << ",\"op\":" << (U.exclusive ? "\"xor\"" : "\"or\"")
       << ",\"width\":" << U.width
       << ",\"matrix\":\"" << toHex(U.matrix) << "\",\"mask\":";
    if (U.maskIsConst)
      ls << '"' << toHex(U.mask) << '"';
    else
      ls << "null";
    if (U.known_zero.getBitWidth() == U.width &&
        (U.known_zero.getBoolValue() || U.known_one.getBoolValue())) {
      ls << ",\"known_zero\":\"" << toHex(U.known_zero)
         << "\",\"known_one\":\"" << toHex(U.known_one) << '"';
    }
    ls << ",\"loop_depth\":" << U.loop_depth
       << ",\"rel_freq\":" << format("%.6g", U.rel_freq);
    ls << ",\"cost_before\":";
    if (costBefore < 0) ls << "null"; else ls << costBefore;
    ls << ",\"cost_after\":";
    if (costAfter < 0) ls << "null"; else ls << costAfter;
    if (!evidence.empty()) {
      ls << ",\"evidence\":";
      writeJSONString(ls, evidence);
    }
    ls << ",\"function\":";
    writeJSONString(ls, config::current_function);
    ls << ",\"source\":";
    writeJSONString(ls, config::current_source);
    ls << ",\"src\":";
    writeJSONSrc(ls, srcIR);
    ls << "}\n";
    ls.flush();
    // one write per record keeps lines intact across concurrent processes
    os << line;
    os.flush();
  }
}

} // namespace minotaur
