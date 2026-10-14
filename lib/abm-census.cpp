// Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
// Distributed under the MIT license that can be found in the LICENSE file.
#include "abm-census.h"
#include "abm-log.h"
#include "config.h"

#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/PostOrderIterator.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/Analysis/BlockFrequencyInfo.h"
#include "llvm/Analysis/LoopInfo.h"
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/IR/CFG.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/IntrinsicInst.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/Format.h"
#include "llvm/Support/KnownBits.h"
#include "llvm/Support/raw_ostream.h"
#include "llvm/Transforms/Utils/ValueMapper.h"

#include <map>
#include <random>
#include <string>
#include <vector>

using namespace llvm;
using namespace std;

namespace minotaur {
namespace {

// How far a region may grow. Full admits the arithmetic that is sometimes
// affine on its reachable inputs (add of disjoint bits, x * 2^k, icmp eq 0);
// Bitwise is the retry when that arithmetic made the whole region nonlinear.
enum class Grow { Full, Bitwise };

bool isIntScalar(const Value *V) { return V->getType()->isIntegerTy(); }

bool constAmount(const Value *V) { return isa<ConstantInt>(V); }

// A call's last operand is its callee, which is not an input of the operation.
unsigned numValueOps(const Instruction *I) {
  if (auto *CI = dyn_cast<CallInst>(I))
    return CI->arg_size();
  return I->getNumOperands();
}

// An instruction the region may absorb: one the interpreter below can run.
bool absorbable(const Instruction *I, Grow G) {
  if (!isIntScalar(I))
    return false;
  for (unsigned k = 0, e = numValueOps(I); k < e; ++k)
    if (!isIntScalar(I->getOperand(k)))
      return false;

  switch (I->getOpcode()) {
  case Instruction::And:
  case Instruction::Or:
  case Instruction::Xor:
  case Instruction::ZExt:
  case Instruction::SExt:
  case Instruction::Trunc:
  case Instruction::Freeze:
    return true;
  case Instruction::Shl:
  case Instruction::LShr:
  case Instruction::AShr:
    return G == Grow::Full || constAmount(I->getOperand(1));
  case Instruction::Add:
  case Instruction::Sub:
  case Instruction::Mul:
  case Instruction::Select:
  case Instruction::ICmp:
    return G == Grow::Full;
  case Instruction::Call: {
    auto *II = dyn_cast<IntrinsicInst>(I);
    if (!II)
      return false;
    switch (II->getIntrinsicID()) {
    case Intrinsic::bswap:
    case Intrinsic::bitreverse:
      return true;
    case Intrinsic::fshl:
    case Intrinsic::fshr:
      return G == Grow::Full || constAmount(II->getArgOperand(2));
    default:
      return false;
    }
  }
  default:
    return false;
  }
}

// A region compiled for fast repeated evaluation on APInts. LLVM's constant
// folder would do, but every sample would leave uniqued constants behind in the
// context, and a census evaluates millions of them.
struct Operand {
  enum Kind { Const, Leaf, Node } K;
  unsigned Idx = 0;
  APInt C;
};

struct Node {
  unsigned Opcode;
  Intrinsic::ID IID = Intrinsic::not_intrinsic;
  CmpInst::Predicate Pred = CmpInst::BAD_ICMP_PREDICATE;
  unsigned Width;
  SmallVector<Operand, 3> Ops;
};

struct Region {
  Instruction *Root = nullptr;
  SmallVector<Instruction *, 32> Insts; // operands before users; Root last
  SmallVector<Value *, 8> Leaves;
  SmallVector<unsigned, 8> Offsets;     // of each leaf within the input word
  unsigned InBits = 0;
  vector<Node> Nodes;                   // parallel to Insts
  Grow G = Grow::Full;
  unsigned Depth = 0;
};

// Grows the region of Root up to Depth operand edges. Values that cannot be
// absorbed become leaves, i.e. inputs of the operation. Fails if a leaf is not
// an integer or the inputs get too wide.
bool growRegion(Instruction *Root, Grow G, unsigned Depth,
                const CensusOptions &O, Region &R) {
  R = Region();
  R.Root = Root;
  R.G = G;
  R.Depth = Depth;

  DenseMap<Instruction *, unsigned> Level;
  SmallVector<Instruction *, 32> Work;
  Level[Root] = 0;
  Work.push_back(Root);
  DenseSet<Value *> LeafSet;

  // breadth first, so a size cap trims the far end of the DAG
  for (unsigned W = 0; W < Work.size(); ++W) {
    Instruction *I = Work[W];
    unsigned L = Level[I];
    for (unsigned k = 0, e = numValueOps(I); k < e; ++k) {
      Value *V = I->getOperand(k);
      if (isa<ConstantInt>(V))
        continue;
      auto *OI = dyn_cast<Instruction>(V);
      if (OI && Level.count(OI))
        continue;
      if (OI && L + 1 <= Depth && Work.size() < O.max_size &&
          absorbable(OI, G) && !isa<PHINode>(OI)) {
        Level[OI] = L + 1;
        Work.push_back(OI);
        continue;
      }
      if (!isIntScalar(V))
        return false;
      if (LeafSet.insert(V).second) {
        R.Leaves.push_back(V);
        R.Offsets.push_back(R.InBits);
        R.InBits += V->getType()->getIntegerBitWidth();
        if (R.InBits > O.max_in_bits)
          return false;
      }
    }
  }
  if (R.Leaves.empty())
    return false;

  // A value absorbed at the depth limit may still have been reached as a leaf
  // through a shorter path first; it is then both, and the leaf wins.
  DenseSet<Instruction *> InRegion;
  for (auto *I : Work)
    if (!LeafSet.count(I))
      InRegion.insert(I);

  // post order from the root gives operands before users
  DenseSet<Instruction *> Done;
  SmallVector<pair<Instruction *, unsigned>, 32> Stack{{Root, 0}};
  while (!Stack.empty()) {
    auto &[I, Next] = Stack.back();
    if (Next < numValueOps(I)) {
      auto *OI = dyn_cast<Instruction>(I->getOperand(Next++));
      if (OI && InRegion.count(OI) && !Done.count(OI))
        Stack.push_back({OI, 0});
      continue;
    }
    if (Done.insert(I).second)
      R.Insts.push_back(I);
    Stack.pop_back();
  }

  DenseMap<Value *, unsigned> LeafIdx, NodeIdx;
  for (unsigned i = 0; i < R.Leaves.size(); ++i)
    LeafIdx[R.Leaves[i]] = i;
  for (unsigned i = 0; i < R.Insts.size(); ++i)
    NodeIdx[R.Insts[i]] = i;

  for (auto *I : R.Insts) {
    Node N;
    N.Opcode = I->getOpcode();
    N.Width = I->getType()->getIntegerBitWidth();
    if (auto *II = dyn_cast<IntrinsicInst>(I))
      N.IID = II->getIntrinsicID();
    if (auto *C = dyn_cast<ICmpInst>(I))
      N.Pred = C->getPredicate();
    for (unsigned k = 0, e = numValueOps(I); k < e; ++k) {
      Value *V = I->getOperand(k);
      Operand Opd;
      if (auto *CI = dyn_cast<ConstantInt>(V)) {
        Opd.K = Operand::Const;
        Opd.C = CI->getValue();
      } else if (auto It = NodeIdx.find(V); It != NodeIdx.end()) {
        Opd.K = Operand::Node;
        Opd.Idx = It->second;
      } else {
        Opd.K = Operand::Leaf;
        Opd.Idx = LeafIdx.lookup(V);
      }
      N.Ops.push_back(std::move(Opd));
    }
    R.Nodes.push_back(std::move(N));
  }
  return true;
}

// Evaluates the region on one input word. Shifts past the width are poison in
// the source; they fail the evaluation and so the screen.
optional<APInt> eval(const Region &R, const APInt &X, vector<APInt> &Vals) {
  Vals.resize(R.Nodes.size());
  auto get = [&](const Operand &O) -> APInt {
    switch (O.K) {
    case Operand::Const:
      return O.C;
    case Operand::Node:
      return Vals[O.Idx];
    case Operand::Leaf: {
      unsigned W = R.Leaves[O.Idx]->getType()->getIntegerBitWidth();
      return X.extractBits(W, R.Offsets[O.Idx]);
    }
    }
    llvm_unreachable("operand kind");
  };

  for (unsigned i = 0; i < R.Nodes.size(); ++i) {
    const Node &N = R.Nodes[i];
    unsigned W = N.Width;
    APInt A = get(N.Ops[0]);
    APInt Res(W, 0);
    switch (N.Opcode) {
    case Instruction::And: Res = A & get(N.Ops[1]); break;
    case Instruction::Or:  Res = A | get(N.Ops[1]); break;
    case Instruction::Xor: Res = A ^ get(N.Ops[1]); break;
    case Instruction::Add: Res = A + get(N.Ops[1]); break;
    case Instruction::Sub: Res = A - get(N.Ops[1]); break;
    case Instruction::Mul: Res = A * get(N.Ops[1]); break;
    case Instruction::Shl:
    case Instruction::LShr:
    case Instruction::AShr: {
      APInt S = get(N.Ops[1]);
      if (S.uge(W))
        return nullopt;
      unsigned Amt = S.getZExtValue();
      Res = N.Opcode == Instruction::Shl    ? A.shl(Amt)
            : N.Opcode == Instruction::LShr ? A.lshr(Amt)
                                            : A.ashr(Amt);
      break;
    }
    case Instruction::ZExt:  Res = A.zext(W); break;
    case Instruction::SExt:  Res = A.sext(W); break;
    case Instruction::Trunc: Res = A.trunc(W); break;
    case Instruction::Freeze: Res = A; break;
    case Instruction::Select:
      Res = A.isOne() ? get(N.Ops[1]) : get(N.Ops[2]);
      break;
    case Instruction::ICmp:
      Res = APInt(1, ICmpInst::compare(A, get(N.Ops[1]), N.Pred));
      break;
    case Instruction::Call:
      switch (N.IID) {
      case Intrinsic::bswap:      Res = A.byteSwap(); break;
      case Intrinsic::bitreverse: Res = A.reverseBits(); break;
      case Intrinsic::fshl:
      case Intrinsic::fshr: {
        APInt B = get(N.Ops[1]);
        unsigned S = get(N.Ops[2]).urem(W);
        if (S == 0)
          Res = N.IID == Intrinsic::fshl ? A : B;
        else if (N.IID == Intrinsic::fshl)
          Res = A.shl(S) | B.lshr(W - S);
        else
          Res = B.lshr(S) | A.shl(W - S);
        break;
      }
      default:
        llvm_unreachable("intrinsic not absorbable");
      }
      break;
    default:
      llvm_unreachable("opcode not absorbable");
    }
    Vals[i] = std::move(Res);
  }
  return Vals.back();
}

struct Affine {
  APInt M, B;          // M is Out x In, row i is output bit Out-1-i
  vector<APInt> Rows;  // the same rows, as In-bit masks
  bool Or = false, Xor = false;
  unsigned Samples = 0;
};

APInt applyRows(const vector<APInt> &Rows, const APInt &X, const APInt &B,
                bool Exclusive) {
  unsigned Out = Rows.size();
  APInt Res(Out, 0);
  for (unsigned i = 0; i < Out; ++i) {
    APInt T = Rows[i] & X;
    if (Exclusive ? (T.popcount() & 1) : !T.isZero())
      Res.setBit(Out - 1 - i);
  }
  return Res ^ B;
}

// Inputs that agree with what the compiler already knows about the leaves.
APInt conform(const APInt &V, const APInt &KZ, const APInt &KO) {
  return (V & ~KZ) | KO;
}

APInt randomWord(unsigned Bits, mt19937_64 &RNG) {
  APInt V(Bits, 0);
  for (unsigned w = 0; w < Bits; w += 64) {
    unsigned Chunk = std::min(64u, Bits - w);
    uint64_t Word = RNG();
    if (Chunk < 64)
      Word &= (uint64_t(1) << Chunk) - 1;
    V.insertBits(APInt(Chunk, Word), w);
  }
  return V;
}

// Recovers M and b from basis vectors over the bits that are not known, after
// a cheap linearity pre-screen, then checks both semirings on random inputs.
optional<Affine> extract(const Region &R, const APInt &KZ, const APInt &KO,
                         vector<APInt> &Vals) {
  unsigned In = R.InBits;
  unsigned Out = R.Root->getType()->getIntegerBitWidth();
  APInt Free = ~(KZ | KO);
  mt19937_64 RNG(0x9E3779B97F4A7C15ULL);

  auto F0 = eval(R, KO, Vals);
  if (!F0)
    return nullopt;

  // Pre-screen on pairs, so nonlinear regions are rejected before paying for
  // In basis evaluations. With g(u) = f(KO|u) ^ f(KO):
  //   XOR affine:  g(u ^ v) = g(u) ^ g(v)
  //   OR affine:   g(u | v) = g(u) | g(v)
  bool MaybeOr = true, MaybeXor = true;
  for (unsigned k = 0; k < 6 && (MaybeOr || MaybeXor); ++k) {
    APInt U = randomWord(In, RNG) & Free, V = randomWord(In, RNG) & Free;
    auto GU = eval(R, KO | U, Vals), GV = eval(R, KO | V, Vals);
    if (!GU || !GV)
      return nullopt;
    APInt gu = *GU ^ *F0, gv = *GV ^ *F0;
    if (MaybeXor) {
      auto GX = eval(R, KO | (U ^ V), Vals);
      if (!GX)
        return nullopt;
      MaybeXor = (*GX ^ *F0) == (gu ^ gv);
    }
    if (MaybeOr) {
      auto GO = eval(R, KO | U | V, Vals);
      if (!GO)
        return nullopt;
      MaybeOr = (*GO ^ *F0) == (gu | gv);
    }
  }
  if (!MaybeOr && !MaybeXor)
    return nullopt;

  Affine A;
  A.B = *F0;
  A.M = APInt(uint64_t(Out) * In, 0);
  A.Rows.assign(Out, APInt(In, 0));
  for (unsigned j = 0; j < In; ++j) {
    if (!Free[j])
      continue;
    APInt E = KO;
    E.setBit(j);
    auto Fj = eval(R, E, Vals);
    if (!Fj)
      return nullopt;
    APInt Col = *Fj ^ A.B;
    for (unsigned o = 0; o < Out; ++o) {
      if (!Col[o])
        continue;
      unsigned i = Out - 1 - o;
      A.M.setBit(uint64_t(i) * In + j);
      A.Rows[i].setBit(j);
    }
  }

  A.Or = MaybeOr;
  A.Xor = MaybeXor;
  for (unsigned k = 0; k < 256 && (A.Or || A.Xor); ++k) {
    APInt X = k == 0 ? APInt::getAllOnes(In) : randomWord(In, RNG);
    X = conform(X, KZ, KO);
    auto FX = eval(R, X, Vals);
    if (!FX)
      return nullopt;
    if (A.Or && applyRows(A.Rows, X, A.B, false) != *FX)
      A.Or = false;
    if (A.Xor && applyRows(A.Rows, X, A.B, true) != *FX)
      A.Xor = false;
    ++A.Samples;
  }
  if (!A.Or && !A.Xor)
    return nullopt;
  return A;
}

// A standalone function computing the region: one argument per leaf, known
// bits imposed on entry, the root returned. This is what gets verified.
string regionIR(const Region &R, const vector<KnownBits> &Known) {
  LLVMContext &Ctx = R.Root->getContext();
  Module M("cut", Ctx);
  M.setDataLayout(R.Root->getModule()->getDataLayout());
  SmallVector<Type *, 8> ArgTys;
  for (auto *L : R.Leaves)
    ArgTys.push_back(L->getType());
  auto *FTy = FunctionType::get(R.Root->getType(), ArgTys, false);
  auto *Fn = Function::Create(FTy, GlobalValue::ExternalLinkage, "src", M);
  auto *BB = BasicBlock::Create(Ctx, "entry", Fn);
  IRBuilder<> IRB(BB);

  ValueToValueMapTy VM;
  for (unsigned i = 0; i < R.Leaves.size(); ++i) {
    Value *A = Fn->getArg(i);
    const KnownBits &K = Known[i];
    if (!K.Zero.isZero())
      A = IRB.CreateAnd(A, ConstantInt::get(A->getType(), ~K.Zero));
    if (!K.One.isZero())
      A = IRB.CreateOr(A, ConstantInt::get(A->getType(), K.One));
    VM[R.Leaves[i]] = A;
  }
  for (auto *I : R.Insts) {
    Instruction *NI = I->clone();
    NI->setDebugLoc(DebugLoc());
    SmallVector<pair<unsigned, MDNode *>, 4> MDs;
    NI->getAllMetadata(MDs);
    for (auto &[Kind, MD] : MDs)
      NI->setMetadata(Kind, nullptr);
    if (auto *CI = dyn_cast<CallInst>(NI)) {
      auto *Callee = CI->getCalledFunction();
      CI->setCalledFunction(
          M.getOrInsertFunction(Callee->getName(), Callee->getFunctionType()));
    }
    IRB.Insert(NI);
    VM[I] = NI;
    RemapInstruction(NI, VM,
                     RF_NoModuleLevelChanges | RF_IgnoreMissingLocals);
  }
  IRB.CreateRet(VM[R.Root]);

  string S;
  raw_string_ostream OS(S);
  Fn->print(OS);
  for (auto &D : M.functions())
    if (D.isDeclaration())
      OS << D;
  OS.flush();
  return S;
}

string toHex(const APInt &V) {
  SmallString<128> S;
  V.toStringUnsigned(S, 16);
  return string(S);
}

string opHistogram(const Region &R) {
  map<string, unsigned> H;
  for (auto *I : R.Insts) {
    if (auto *II = dyn_cast<IntrinsicInst>(I))
      ++H[Intrinsic::getBaseName(II->getIntrinsicID()).str()];
    else
      ++H[I->getOpcodeName()];
  }
  string S;
  for (auto &[K, N] : H) {
    if (!S.empty())
      S += ',';
    S += K + ":" + to_string(N);
  }
  return S;
}

struct Found {
  Region R;
  vector<KnownBits> Known;
  optional<Affine> A;   // set for kind "census"
  APInt Table;          // set for kind "table"
  unsigned TableBits = 0;
};

void writeRecord(raw_ostream &OS, const Found &F, const LoopInfo &LI,
                 const BlockFrequencyInfo *BFI, double EntryFreq) {
  const Region &R = F.R;
  unsigned Out = R.Root->getType()->getIntegerBitWidth();
  BasicBlock *BB = R.Root->getParent();
  double Freq = 1.0;
  if (BFI && EntryFreq > 0.0)
    Freq = (double)BFI->getBlockFreq(BB).getFrequency() / EntryFreq;

  string Line;
  raw_string_ostream L(Line);
  L << "{\"kind\":\"" << (F.A ? "census" : "table") << '"';
  if (F.A) {
    bool Single = true;
    for (auto &Row : F.A->Rows)
      Single &= Row.popcount() <= 1;
    const char *Op = Single || (F.A->Or && F.A->Xor) ? "any"
                     : F.A->Xor                       ? "xor"
                                                      : "or";
    L << ",\"op\":\"" << Op << '"';
  }
  L << ",\"width\":" << Out << ",\"in_width\":" << R.InBits;
  if (F.A) {
    L << ",\"matrix\":\"" << toHex(F.A->M) << "\",\"mask\":\""
      << toHex(F.A->B) << '"';
  } else {
    L << ",\"table_bits\":" << F.TableBits << ",\"table\":\""
      << toHex(F.Table) << '"';
  }
  L << ",\"inputs\":[";
  for (unsigned i = 0; i < R.Leaves.size(); ++i) {
    if (i)
      L << ',';
    unsigned W = R.Leaves[i]->getType()->getIntegerBitWidth();
    L << "{\"w\":" << W << ",\"kz\":\"" << toHex(F.Known[i].Zero)
      << "\",\"ko\":\"" << toHex(F.Known[i].One) << "\",\"from\":\""
      << (isa<Argument>(R.Leaves[i])  ? "arg"
          : isa<PHINode>(R.Leaves[i]) ? "phi"
          : isa<LoadInst>(R.Leaves[i]) ? "load"
          : isa<Instruction>(R.Leaves[i])
              ? cast<Instruction>(R.Leaves[i])->getOpcodeName()
              : "const")
      << "\"}";
  }
  L << "],\"region_size\":" << R.Insts.size() << ",\"region_ops\":\""
    << opHistogram(R) << "\",\"grow\":\""
    << (R.G == Grow::Full ? "full" : "bitwise") << "\",\"depth\":" << R.Depth
    << ",\"loop_depth\":" << LI.getLoopDepth(BB)
    << ",\"rel_freq\":" << llvm::format("%.6g", Freq);
  if (F.A)
    L << ",\"evidence\":\"basis+" << F.A->Samples << "-samples\"";
  L << ",\"function\":";
  writeJSONString(L, config::current_function);
  L << ",\"source\":";
  writeJSONString(L, config::current_source);
  L << ",\"cut\":";
  writeJSONString(L, regionIR(R, F.Known));
  L << "}\n";
  L.flush();
  // one write per record keeps lines whole across concurrent processes
  OS << Line;
  OS.flush();
}

} // namespace

unsigned runABMCensus(Function &F, const LoopInfo &LI, const DominatorTree &DT,
                      const BlockFrequencyInfo *BFI, const CensusOptions &O) {
  if (config::abm_log_path.empty() || F.isDeclaration())
    return 0;
  const DataLayout &DL = F.getParent()->getDataLayout();

  // Users before definitions, so a value already inside a found region can be
  // skipped as subsumed instead of analysed again.
  vector<Instruction *> Roots;
  for (BasicBlock *BB : post_order(&F.getEntryBlock()))
    for (Instruction &I : reverse(*BB))
      Roots.push_back(&I);

  vector<Found> Results;
  DenseSet<Instruction *> Covered; // non-root members of affine regions
  vector<APInt> Vals;

  for (Instruction *Root : Roots) {
    if (O.deadline && std::chrono::steady_clock::now() > *O.deadline)
      break;
    if (!isIntScalar(Root) || isa<PHINode>(Root))
      continue;
    if (Root->getType()->getIntegerBitWidth() > O.max_out_bits)
      continue;
    if (!O.log_subsumed && Covered.count(Root))
      continue;
    if (!absorbable(Root, Grow::Full))
      continue;

    // largest first: the full region, then without arithmetic, then shallower
    SmallVector<pair<Grow, unsigned>, 8> Tries{{Grow::Full, O.max_depth},
                                               {Grow::Bitwise, O.max_depth}};
    for (unsigned D : {8u, 6u, 4u, 3u, 2u, 1u})
      if (D < O.max_depth)
        Tries.push_back({Grow::Full, D});

    bool Tabled = false;
    for (auto [G, D] : Tries) {
      if (G == Grow::Bitwise && !absorbable(Root, Grow::Bitwise))
        continue;
      Region R;
      if (!growRegion(Root, G, D, O, R))
        continue;
      if (R.Insts.size() < O.min_size)
        continue;

      vector<KnownBits> Known;
      APInt KZ(R.InBits, 0), KO(R.InBits, 0);
      for (unsigned i = 0; i < R.Leaves.size(); ++i) {
        KnownBits K = computeKnownBits(R.Leaves[i], DL, nullptr, Root, &DT);
        KZ.insertBits(K.Zero, R.Offsets[i]);
        KO.insertBits(K.One, R.Offsets[i]);
        Known.push_back(std::move(K));
      }

      if (auto A = extract(R, KZ, KO, Vals)) {
        if (A->M.isZero())
          break; // constant on every reachable input: nothing to name
        for (auto *I : R.Insts)
          if (I != Root)
            Covered.insert(I);
        Found Fd;
        Fd.R = std::move(R);
        Fd.Known = std::move(Known);
        Fd.A = std::move(A);
        Results.push_back(std::move(Fd));
        break;
      }

      // Not affine, but with few enough reachable input bits it is a lookup
      // table, which is worth knowing too. Only the largest region is tabled.
      unsigned FreeBits = R.InBits - (KZ | KO).popcount();
      unsigned Out = Root->getType()->getIntegerBitWidth();
      if (!Tabled && O.table_bits && FreeBits <= O.table_bits) {
        Tabled = true;
        SmallVector<unsigned, 8> FreePos;
        for (unsigned j = 0; j < R.InBits; ++j)
          if (!KZ[j] && !KO[j])
            FreePos.push_back(j);
        APInt T(Out << FreeBits, 0);
        bool Ok = true;
        for (uint64_t e = 0; e < (uint64_t(1) << FreeBits) && Ok; ++e) {
          APInt X = KO;
          for (unsigned b = 0; b < FreeBits; ++b)
            if (e >> b & 1)
              X.setBit(FreePos[b]);
          auto V = eval(R, X, Vals);
          if (!V)
            Ok = false;
          else
            T.insertBits(*V, e * Out);
        }
        if (Ok) {
          Found Fd;
          Fd.R = R;
          Fd.Known = Known;
          Fd.Table = std::move(T);
          Fd.TableBits = FreeBits;
          Results.push_back(std::move(Fd));
        }
      }
    }
  }

  error_code EC;
  raw_fd_ostream OS(config::abm_log_path, EC,
                    sys::fs::OF_Append | sys::fs::OF_Text);
  if (EC) {
    config::dbg() << "[abm-census] cannot open " << config::abm_log_path
                  << ": " << EC.message() << "\n";
    return 0;
  }
  // unbuffered: a record longer than the buffer would otherwise go out in
  // several writes and interleave with other processes appending to the log
  OS.SetUnbuffered();
  double EntryFreq =
      BFI ? (double)BFI->getEntryFreq().getFrequency() : 0.0;
  unsigned N = 0;
  for (auto &Fd : Results) {
    // a table nested in a larger affine region is part of that operation
    if (!O.log_subsumed && Covered.count(Fd.R.Root) && !Fd.A)
      continue;
    writeRecord(OS, Fd, LI, BFI, EntryFreq);
    ++N;
  }
  return N;
}

} // namespace minotaur
