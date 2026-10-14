// Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
// Distributed under the MIT license that can be found in the LICENSE file.
#include "alive-interface.h"
#include "abm-log.h"
#include "config.h"
#include "enumerator.h"
#include "expr.h"
#include "codegen.h"
#include "cost.h"
#include "utils.h"
#include "type.h"

#include "util/compiler.h"
#include "llvm_util/llvm2alive.h"
#include "llvm_util/utils.h"

#include "llvm/ADT/APInt.h"
#include "llvm/ADT/Statistic.h"
#include "llvm/Analysis/CGSCCPassManager.h"
#include "llvm/Analysis/LoopAnalysisManager.h"
#include "llvm/Analysis/ScalarEvolutionExpressions.h"
#include "llvm/Analysis/TargetLibraryInfo.h"
#include "llvm/Analysis/ConstantFolding.h"
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/IR/Constant.h"
#include "llvm/IR/DataLayout.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/BasicBlock.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Verifier.h"
#include "llvm/Support/raw_ostream.h"
#include "llvm/TargetParser/Triple.h"
#include "llvm/Transforms/Utils/Cloning.h"
#include "llvm/Support/KnownBits.h"

#include <cstdint>
#include <algorithm>
#include <memory>
#include <vector>
#include <set>
#include <map>
#include <limits>
#include <random>

using namespace tools;
using namespace util;
using namespace std;
using namespace IR;

namespace {
struct debug {
template<class T>
debug &operator<<(const T &s)
{
if (minotaur::config::debug_enumerator)
  minotaur::config::dbg() << s;
return *this;
}
};
}


namespace minotaur {

void Enumerator::findInputs(llvm::Function &F,
                            llvm::Instruction *root,
                            llvm::DominatorTree &DT) {
  for (auto &A : F.args()) {
    // the slicer appends an unused i16 argument to every cut; it is not a
    // value of the original program and codegen cannot map it back
    if (A.use_empty())
      continue;
    auto T = make_unique<Var>(&A);
    values.emplace_back(T.get());
    exprs.emplace_back(std::move(T));
  }
  for (auto &BB : F) {
    for (auto &I : BB) {
      if (&I == root)
        continue;

      auto ty = I.getType()->getScalarType();
      if (!ty->isIntegerTy() && !ty->isIEEELikeFPTy())
        continue;

      if (!DT.dominates(&I, root))
        continue;

      auto T = make_unique<Var>(&I);
      values.emplace_back(T.get());
      exprs.emplace_back(std::move(T));
    }
  }
}

bool Enumerator::getSketches(llvm::Value *V, vector<Sketch> &sketches) {
  vector<Value*> Comps;
  for (auto &I : values) {
    Comps.emplace_back(I);
  }

  type expected{V->getType()};

  // casts
  for (auto Comp : Comps) {
    auto Op = dynamic_cast<Var*>(Comp);
    if (!Op)
      continue;

    unsigned op_w = Op->getType().getWidth();
    if (Op->getType().same_width(expected))
      continue;

    vector<type> tys = getIntegerVectorTypes(Op->getType());
    for (auto workty : tys) {
      unsigned op_bits = workty.getBits();
      unsigned lane = workty.getLane();
      if (expected.getWidth() % lane != 0)
        continue;

      if (expected.getWidth() > op_w) {
        if (expected.getWidth() % op_w)
          continue;
        unsigned nb = (expected.getWidth() / op_w) * op_bits;
        set<ReservedConst*> RCs1;
        auto SI = make_unique<IntConversion>(IntConversion::sext, *Op, lane,
                                             op_bits, nb);
        sketches.push_back(make_pair(SI.get(), std::move(RCs1)));
        exprs.emplace_back(std::move(SI));
        set<ReservedConst*> RCs2;
        auto ZI = make_unique<IntConversion>(IntConversion::zext, *Op, lane,
                                             op_bits, nb);
        sketches.push_back(make_pair(ZI.get(), std::move(RCs2)));
        exprs.emplace_back(std::move(ZI));
      } else if (expected.getWidth() < op_w) {
        if (op_w % expected.getWidth() != 0)
          continue;

        unsigned nb = expected.getWidth() * op_bits / op_w;
        if (nb == 0)
          continue;
        set<ReservedConst*> RCs1;
        auto SI = make_unique<IntConversion>(IntConversion::trunc, *Op, lane,
                                             op_bits, nb);
        sketches.push_back(make_pair(SI.get(), std::move(RCs1)));
        exprs.emplace_back(std::move(SI));
      }
    }
  }

  for (auto Comp : Comps) {
    auto Op = dynamic_cast<Var*>(Comp);
    if (!Op)
      continue;

    auto op_ty = Op->getType();
    if (expected.isFP() && op_ty.isFP()) {
      if (expected.getLane() != op_ty.getLane())
        continue;
      if (expected.getBits() > op_ty.getBits()) {
        set<ReservedConst*> RCs;
        auto SI = make_unique<FPConversion>(FPConversion::fpext, *Op, expected);
        sketches.push_back(make_pair(SI.get(), std::move(RCs)));
        exprs.emplace_back(std::move(SI));
      } else if (expected.getBits() < op_ty.getBits()) {
        set<ReservedConst*> RCs;
        auto SI = make_unique<FPConversion>(FPConversion::fptrunc, *Op, expected);
        sketches.push_back(make_pair(SI.get(), std::move(RCs)));
        exprs.emplace_back(std::move(SI));
      }
    }

    if (expected.isFP() ^ op_ty.isFP()) {
      if (op_ty.isFP()) {
        if (expected.getWidth() % op_ty.getLane())
          continue;
        set<ReservedConst*> RCs;
        auto SI = make_unique<FPConversion>(FPConversion::fptosi, *Op, expected);
        sketches.push_back(make_pair(SI.get(), std::move(RCs)));
        exprs.emplace_back(std::move(SI));
        set<ReservedConst*> RCs2;
        auto UI = make_unique<FPConversion>(FPConversion::fptoui, *Op, expected);
        sketches.push_back(make_pair(UI.get(), std::move(RCs2)));
        exprs.emplace_back(std::move(UI));
      } else if (expected.isFP()) {
        if (op_ty.getWidth() % expected.getLane())
          continue;
        set<ReservedConst*> RCs;
        auto SI = make_unique<FPConversion>(FPConversion::uitofp, *Op, expected);
        sketches.push_back(make_pair(SI.get(), std::move(RCs)));
        exprs.emplace_back(std::move(SI));
        set<ReservedConst*> RCs2;
        auto UI = make_unique<FPConversion>(FPConversion::sitofp, *Op, expected);
        sketches.push_back(make_pair(UI.get(), std::move(RCs2)));
        exprs.emplace_back(std::move(UI));
      }
    }
  }

  // unop
  for (auto Op0 : Comps) {
    if (!expected.same_width(Op0->getType()))
      continue;
    for (unsigned K = UnaryOp::bitreverse; K <= UnaryOp::ftrunc; ++K) {
      UnaryOp::Op opcode = static_cast<UnaryOp::Op>(K);
      vector<type> tys = getUnaryOpWorkTypes(expected, opcode);

      for (auto workty : tys) {
        set<ReservedConst*> RCs;
        auto U = make_unique<UnaryOp>(opcode, *Op0, workty);
        sketches.push_back(make_pair(U.get(), std::move(RCs)));
        exprs.emplace_back(std::move(U));
      }
    }
  }

  // extractelement
  for (auto Op0 : Comps) {
    auto op0_ty = Op0->getType();
    if (op0_ty.getWidth() <= expected.getWidth())
      continue;
    if (op0_ty.getWidth() % expected.getWidth())
      continue;
    if (op0_ty.isFP() ^ expected.isFP())
      continue;
    if (op0_ty.isFP()) {
      if (expected.getLane() != 1)
        continue;
      if (op0_ty.getBits() != expected.getBits())
        continue;
    }

    auto T = make_unique<ReservedConst>(type::Integer(16));
    ReservedConst *idx = T.get();
    auto ety = type::Scalar(expected.getWidth(), expected.isFP());
    set<ReservedConst*> RCs;
    RCs.insert(T.get());
    exprs.emplace_back(std::move(T));
    auto EE = make_unique<ExtractElement>(*Op0, *idx, ety);
    sketches.push_back(make_pair(EE.get(), std::move(RCs)));
    exprs.emplace_back(std::move(EE));
  }

  auto RC1 = make_unique<ReservedConst>(type::Null());
  Comps.emplace_back(RC1.get());

  // binop
  for (unsigned K = BinaryOp::band; K <= BinaryOp::copysign; ++K) {
    BinaryOp::Op Op = static_cast<BinaryOp::Op>(K);

    if (expected.getBits() == 1 && !BinaryOp::isLogical(Op)) {
      continue;
    }

    for (auto Op0 = Comps.begin(); Op0 != Comps.end(); ++Op0) {

      auto Op1 = Comps.begin();
      if (K == BinaryOp::Op::mul || K == BinaryOp::Op::fmul)
        Op1 = Op0;
      else if (BinaryOp::isCommutative(Op))
        Op1 = Op0 + 1;

      for (; Op1 != Comps.end(); ++Op1) {

        for (auto workty : getBinaryOpWorkTypes(expected, Op)) {
          Value *I = nullptr, *J = nullptr;
          set<ReservedConst*> RCs;

          // (op rc, var)
          if (dynamic_cast<ReservedConst*>(*Op0)) {
            if (auto R = dynamic_cast<Var*>(*Op1)) {
              if (!expected.same_width(R->getType()))
                continue;
              auto T = make_unique<ReservedConst>(workty);
              I = T.get();
              RCs.insert(T.get());
              exprs.emplace_back(std::move(T));
              J = R;
            } else continue;
          }
          // (op var, rc), for commutative operations, rc is always in rhs
          else if (dynamic_cast<ReservedConst *>(*Op1)) {
            if (auto L = dynamic_cast<Var *>(*Op0)) {
              // do not generate (- x 3) which can be represented as (+ x -3)
              if (Op == BinaryOp::Op::sub)
                continue;
              if (!expected.same_width(L->getType()))
                continue;
              I = L;
              auto T = make_unique<ReservedConst>(workty);
              J = T.get();
              RCs.insert(T.get());
              exprs.emplace_back(std::move(T));
            } else continue;
          }
          // (op var, var)
          else {
            if (auto L = dynamic_cast<Var *>(*Op0)) {
              if (auto R = dynamic_cast<Var *>(*Op1)) {
                if (!expected.same_width(L->getType()) ||
                    !expected.same_width(R->getType()))
                  continue;
              };
            };
            I = *Op0;
            J = *Op1;
          }
          auto BO = make_unique<BinaryOp>(Op, *I, *J, workty);
          sketches.push_back(make_pair(BO.get(), std::move(RCs)));
          exprs.emplace_back(std::move(BO));
        }
      }
    }
  }

  // ABM: rhs expected to be integer scalar, matrix operand is width^2.
  // Synthesizing the matrix gets expensive as width grows, so each semiring is
  // capped separately; see config::abm_max_width / abm_xor_max_width.
  vector<ABM::Op> abm_ops;
  // An i1 "matrix" is a single and/or/xor priced as one ABM, which is noise.
  if (!expected.isFP() && expected.getLane() == 1 && expected.getBits() > 1) {
    if ((config::abm_mode & config::ABM_MODE_OR) &&
        expected.getBits() <= config::abm_max_width)
      abm_ops.push_back(ABM::OR);
    if ((config::abm_mode & config::ABM_MODE_XOR) &&
        expected.getBits() <= config::abm_xor_max_width)
      abm_ops.push_back(ABM::XOR);
  }
  if (!abm_ops.empty()) {
    uint64_t matBits = uint64_t(expected.getBits()) * expected.getBits();
    if (matBits <= std::numeric_limits<unsigned>::max()) {
      type matTy = type::Integer(static_cast<unsigned>(matBits));

      // Collect B candidates. The fresh constant goes first so that ties are
      // broken toward ABM(x, M, const): a rewrite that reuses a live value as
      // the mask always has an equivalent constant-mask form, and only the
      // latter names a matrix that is comparable across programs.
      vector<pair<Value*, set<ReservedConst*>>> bCandidates;
      {
        auto T = make_unique<ReservedConst>(expected);
        auto *BConst = T.get();
        set<ReservedConst*> RCs;
        RCs.insert(BConst);
        bCandidates.push_back({BConst, RCs});
        exprs.emplace_back(std::move(T));
      }
      for (auto B : Comps) {
        auto bty = B->getType();
        if (!bty.isValid() || bty.isFP() || bty.isVector())
          continue;
        if (!bty.same_width(expected))
          continue;
        bCandidates.push_back({B, {}});
      }

      // collect M candidates (fresh constant first, same reasoning)
      vector<pair<Value*, set<ReservedConst*>>> m_candidates;
      {
        // auto *MVal = llvm::Constant::getIntegerValue(matTy.toLLVM(V->getContext()), llvm::APInt(matBits, 9078117754732544L));
        auto T = make_unique<ReservedConst>(matTy/*, MVal*/);
        auto *MConst = T.get();
        set<ReservedConst*> RCs;
        RCs.insert(MConst);
        m_candidates.push_back({MConst, RCs});
        exprs.emplace_back(std::move(T));
      }
      for (auto M : Comps) {
        auto mty = M->getType();
        if (!mty.isValid() || mty.isFP() || mty.isVector())
          continue;
        if (!mty.same_width(matTy))
          continue;
        m_candidates.push_back({M, {}});
      }

      for (auto X : Comps) {
        auto xty = X->getType();
        if (!xty.isValid() || xty.isFP() || xty.isVector())
          continue;
        if (!xty.same_width(expected))
          continue;

        for (auto &[BVal, b_rcs] : bCandidates) {
          for (auto &[MVal, m_rcs] : m_candidates) {
            // one sketch per semiring; the constants below are cloned per op
            // so the OR and XOR candidates never share reserved constants
            for (auto abm_op : abm_ops) {
            set<ReservedConst*> RCs;

            // Clone reserved constants so each sketch owns its own copy and
            // later constant synthesis can't overwrite shared state across
            // different ABM candidates.
            Value *BUse = BVal;
            if (auto *BRC = dynamic_cast<ReservedConst*>(BVal)) {
              auto T = make_unique<ReservedConst>(BRC->getType());
              BUse = T.get();
              RCs.insert(T.get());
              exprs.emplace_back(std::move(T));
            } else {
              RCs.insert(b_rcs.begin(), b_rcs.end());
            }

            Value *MUse = MVal;
            if (auto *MRC = dynamic_cast<ReservedConst*>(MVal)) {
              auto T = make_unique<ReservedConst>(MRC->getType());
              MUse = T.get();
              RCs.insert(T.get());
              exprs.emplace_back(std::move(T));
            } else {
              RCs.insert(m_rcs.begin(), m_rcs.end());
            }

            auto abm = make_unique<ABM>(*X, *MUse, *BUse, abm_op);
            sketches.push_back(make_pair(abm.get(), std::move(RCs)));
            exprs.emplace_back(std::move(abm));
            }
          }
        }
      }
    }
  }

  //icmps
  if (expected.getWidth() <= 64) {
    unsigned lanes = expected.getWidth();
    for (auto Op0 = Comps.begin(); Op0 != Comps.end(); ++Op0) {
      for (auto Op1 = Comps.begin(); Op1 != Comps.end(); ++Op1) {
        // skip (icmp op, op)
        if (Op0 == Op1)
          continue;
        // skip (icmp rc1, rc2)
        if (dynamic_cast<ReservedConst*>(*Op0) &&
            dynamic_cast<ReservedConst*>(*Op1))
          continue;
        // skip (icmp rc, var)
        if (dynamic_cast<ReservedConst*>(*Op0) && dynamic_cast<Var*>(*Op1))
          continue;

        //icmps
        for (unsigned C = ICmp::Cond::eq; C <= ICmp::Cond::sge; ++C) {
          ICmp::Cond Cond = static_cast<ICmp::Cond>(C);
          set<ReservedConst*> RCs;
          Value *I = nullptr, *J = nullptr;

          if (auto L = dynamic_cast<Var*>(*Op0)) {
            if (L->getType().getWidth() % expected.getWidth())
              continue;

            unsigned elem_bits = L->getType().getWidth() / lanes;
            if (elem_bits != 8 && elem_bits != 16 &&
                elem_bits != 32 && elem_bits != 64)
              continue;
            // (icmp var, rc)
            if (dynamic_cast<ReservedConst*>(*Op1)) {
              if (Cond == ICmp::sle || Cond == ICmp::ule)
                continue;
              I = L;
              auto jty = type::IntegerVectorizable(lanes, elem_bits);
              auto T = make_unique<ReservedConst>(jty);
              J = T.get();
              RCs.insert(T.get());
              exprs.emplace_back(std::move(T));
            // (icmp var, var)
            } else if (auto R = dynamic_cast<Var*>(*Op1)) {
              if (L->getType().getWidth() != R->getType().getWidth())
                continue;
              I = *Op0;
              J = *Op1;
            } else UNREACHABLE();
          } else UNREACHABLE();
          auto BO = make_unique<ICmp>(Cond, *I, *J, lanes);
          sketches.push_back(make_pair(BO.get(), std::move(RCs)));
          exprs.emplace_back(std::move(BO));
        }
      }
    }
  }

  // fcmps
  if (expected.getWidth() <= 64) {
    unsigned lanes = expected.getWidth();
    for (auto Op0 = Comps.begin(); Op0 != Comps.end(); ++Op0) {
      for (auto Op1 = Comps.begin(); Op1 != Comps.end(); ++Op1) {
        if (Op0 == Op1)
          continue;
        // skip (fcmp rc, rc)
        if (dynamic_cast<ReservedConst*>(*Op0) &&
            dynamic_cast<ReservedConst*>(*Op1))
          continue;
        // skip (fcmp rc, var)
        if (dynamic_cast<ReservedConst*>(*Op0) && dynamic_cast<Var*>(*Op1))
          continue;

        //fcmps
        Value *I = dynamic_cast<Var*>(*Op0);
        if (!I)
          continue;

        if (!I->getType().isFP())
          continue;

        if (I->getType().getLane() != expected.getWidth())
          continue;

        if (auto V = dynamic_cast<Var*>(*Op1)) {
          if (I->getType() != V->getType())
            continue;
        }

        for (unsigned C = FCmp::Cond::f; C <= FCmp::Cond::t; ++C) {
          FCmp::Cond Cond = static_cast<FCmp::Cond>(C);
          set<ReservedConst*> RCs;

          Value *J = nullptr;

          if (dynamic_cast<Var*>(*Op1)) {
            J = *Op1;
          } else if (dynamic_cast<ReservedConst*>(*Op1)) {
            auto T = make_unique<ReservedConst>(I->getType());
            J = T.get();
            RCs.insert(T.get());
            exprs.emplace_back(std::move(T));
          } else UNREACHABLE();

          auto BO = make_unique<FCmp>(Cond, *I, *J, lanes);
          sketches.push_back(make_pair(BO.get(), std::move(RCs)));
          exprs.emplace_back(std::move(BO));
        }
      }
    }
  }

  // insertelement
  for (auto Op0 : Comps) {
    for (auto Op1 : Comps) {
      if (dynamic_cast<ReservedConst*>(Op1)) {
        Value *V = Op0;
        auto v_ty = Op0->getType();
        if (v_ty.getWidth() != expected.getWidth())
          continue;
        auto worktys = getInsertElementWorkTypes(expected);
        for (auto ty : worktys) {
          set<ReservedConst*> RCs;
          auto T1 = make_unique<ReservedConst>(ty.getAsScalar());
          Value *Elm = T1.get();
          RCs.insert(T1.get());
          exprs.emplace_back(std::move(T1));

          auto T2 = make_unique<ReservedConst>(type::Integer(16));
          ReservedConst *idx = T2.get();
          RCs.insert(T2.get());
          exprs.emplace_back(std::move(T2));
          auto IE = make_unique<InsertElement>(*V, *Elm, *idx, ty);
          sketches.push_back(make_pair(IE.get(), std::move(RCs)));
          exprs.emplace_back(std::move(IE));
        }
      } else {
        Value *V = Op0, *Elm = Op1;
        set<ReservedConst*> RCs;
        if (dynamic_cast<ReservedConst*>(Op0)) {
          auto T = make_unique<ReservedConst>(expected);
          V = T.get();
          RCs.insert(T.get());
          exprs.emplace_back(std::move(T));
        }
        type v_ty = V->getType();
        type elm_ty = Elm->getType();

        if (v_ty.getWidth() != expected.getWidth())
          continue;
        if (elm_ty.getWidth() >= v_ty.getWidth())
          continue;
        if (v_ty.getWidth() % elm_ty.getWidth())
          continue;
        if (elm_ty.getWidth() < 8)
          continue;
        if (v_ty.isFP() ^ elm_ty.isFP())
          continue;
        if (elm_ty.isFP()) {
          if (elm_ty.getLane() != 1)
            continue;
          if (v_ty.getBits() != elm_ty.getBits())
            continue;
        } else {
          auto lane = v_ty.getWidth() / elm_ty.getWidth();
          auto bits = elm_ty.getWidth();
          v_ty = type::IntegerVectorizable(lane, bits);
          elm_ty = type::Integer(bits);
        }

        auto T = make_unique<ReservedConst>(type::Integer(16));
        ReservedConst *idx = T.get();
        RCs.insert(T.get());
        exprs.emplace_back(std::move(T));
        auto IE = make_unique<InsertElement>(*V, *Elm, *idx, elm_ty);
        sketches.push_back(make_pair(IE.get(), std::move(RCs)));
        exprs.emplace_back(std::move(IE));
      }
    }
  }

  for (unsigned K = 0; K < numOfX86BinOpIntrinsics; ++K) {
    if (expected.isFP())
      continue;
    // typecheck for return val
    X86IntrinBinOp::Op op = static_cast<X86IntrinBinOp::Op>(K);
    if (config::disable_avx512 && SIMDBinOpInst::is512(op))
      continue;
    type ret_ty = getIntrinsicRetTy(op);
    type op0_ty = getIntrinsicOp0Ty(op);
    type op1_ty = getIntrinsicOp1Ty(op);

    if (!ret_ty.same_width(expected))
      continue;

    for (auto Op0 = Comps.begin(); Op0 != Comps.end(); ++Op0) {
      for (auto Op1 = Comps.begin(); Op1 != Comps.end(); ++Op1) {
        if (dynamic_cast<ReservedConst *>(*Op0) &&
            dynamic_cast<ReservedConst *>(*Op1))
          continue;

        Value *I = nullptr;
        set<ReservedConst*> RCs;

        if (auto L = dynamic_cast<Var *> (*Op0)) {
          // typecheck for op0
          if (!L->getType().same_width(op0_ty))
            continue;
          I = L;
        } else if (dynamic_cast<ReservedConst *>(*Op0)) {
          auto T = make_unique<ReservedConst>(op0_ty);
          I = T.get();
          RCs.insert(T.get());
          exprs.emplace_back(std::move(T));
        }
        Value *J = nullptr;
        if (auto R = dynamic_cast<Var *>(*Op1)) {
          // typecheck for op1
          if (!R->getType().same_width(op1_ty))
            continue;
          J = R;
        } else if (dynamic_cast<ReservedConst *>(*Op1)) {
          auto T = make_unique<ReservedConst>(op1_ty);
          J = T.get();
          RCs.insert(T.get());
          exprs.emplace_back(std::move(T));
        }
        auto B = make_unique<SIMDBinOpInst>(op, *I, *J);
        sketches.push_back(make_pair(B.get(), std::move(RCs)));
        exprs.emplace_back(std::move(B));
      }
    }
  }

  // shufflevector
  for (auto Op0 = Comps.begin(); Op0 != Comps.end(); ++Op0) {
    // skip (sv rc, *, mask)
    if (dynamic_cast<ReservedConst *>(*Op0))
      continue;

    type op_ty = (*Op0)->getType();

    //skip if expected and op_ty are not both fp or int
    if (expected.isFP() ^ op_ty.isFP())
      continue;

    auto tys = getShuffleWorkTypes(expected);
    for (auto ty : tys) {
      if (ty.getLane() == 1)
        continue;
      type mask_ty = type::IntegerVectorizable(ty.getLane(), 32);


      if (op_ty.getWidth() % ty.getBits())
        continue;
      if (op_ty.getWidth() == ty.getBits())
        continue;

      // (sv var, poison, mask)
      {
        set<ReservedConst*> RCs;
        auto m = make_unique<ReservedConst>(mask_ty);
        RCs.insert(m.get());
        auto sv = make_unique<FakeShuffleInst>(**Op0, nullptr, *m.get(), ty);
        exprs.emplace_back(std::move(m));
        sketches.push_back(make_pair(sv.get(), std::move(RCs)));
        exprs.emplace_back(std::move(sv));
      }
      // (sv var1, var2, mask)
      for (auto Op1 = Op0 + 1; Op1 != Comps.end(); ++Op1) {
        set<ReservedConst*> RCs;
        Value *J = nullptr;
        if (auto R = dynamic_cast<Var *>(*Op1)) {
          // typecheck for op1
          if (!op_ty.same_width(R->getType()))
            continue;
          J = R;
        } else if (dynamic_cast<ReservedConst *>(*Op1)) {
          unsigned lanes = (*Op0)->getType().getWidth() / ty.getBits();
          type op_ty = type::IntegerVectorizable(lanes, ty.getBits());
          auto T = make_unique<ReservedConst>(op_ty);
          J = T.get();
          RCs.insert(T.get());
          exprs.emplace_back(std::move(T));
        }
        auto m = make_unique<ReservedConst>(mask_ty);
        RCs.insert(m.get());
        auto sv2 = make_unique<FakeShuffleInst>(**Op0, J, *m.get(), ty);
        exprs.emplace_back(std::move(m));
        sketches.push_back(make_pair(sv2.get(), std::move(RCs)));
        exprs.emplace_back(std::move(sv2));
      }
    }
  }

  // adding new reserved constants for ternary operators
  auto RC2 = make_unique<ReservedConst>(type::Null());
  Comps.emplace_back(RC2.get());

  // select (i1, op, op)
  for (auto Op0 : Comps) {
    for (auto Op1 : Comps) {
      if (Op0 == Op1)
        continue;
      type op0_ty = Op0->getType(), op1_ty = Op1->getType();
      if (expected.isFP()) {
        // exact match for FP operations
        if (op0_ty.isValid() && op0_ty != expected)
          continue;
        if (op1_ty.isValid() && op1_ty != expected)
          continue;
      } else {
        // for integer operations, only check width match
        if (op0_ty.isValid() && !op0_ty.same_width(expected))
          continue;
        if (op1_ty.isValid() && !op1_ty.same_width(expected))
          continue;
      }

      for (auto Cond : Comps) {
        if (dynamic_cast<ReservedConst*>(Cond))
          continue;

        if (!Cond->getType().isBool())
          continue;

        set<ReservedConst*> RCs;
        Value *I = nullptr, *J = nullptr;

        if (dynamic_cast<ReservedConst*>(Op0)) {
          if (Op0 != RC1.get())
            continue;
          auto T = make_unique<ReservedConst>(expected);
          RCs.insert(T.get());
          I = T.get();
          exprs.emplace_back(std::move(T));
        } else {
          I = Op0;
        }

        if (dynamic_cast<ReservedConst*>(Op1)) {
          if (Op1 != RC2.get())
            continue;
          auto T = make_unique<ReservedConst>(expected);
          RCs.insert(T.get());
          J = T.get();
          exprs.emplace_back(std::move(T));
        } else {
          J = Op1;
        }

        auto s = make_unique<Select>(*Cond, *I, *J);
        sketches.push_back(make_pair(s.get(), std::move(RCs)));
        exprs.emplace_back(std::move(s));
      }
    }
  }

  return true;
}

using Candidate = tuple<llvm::Function*, llvm::Function*, Inst*,
                        unordered_map<const llvm::Argument*, ReservedConst*>,
                        bool>;

static bool approx(const Candidate &f1, const Candidate &f2) {
  return get_approx_cost(get<0>(f1)) < get_approx_cost(get<0>(f2));
}


// ---------------------------------------------------------------------------
// Basis-vector extraction
//
// ABM is linear: ABM(x, M, b) = (M (.) x) ^ b. On a basis vector e_j the matrix
// product selects exactly column j, and a single term leaves nothing for OR or
// XOR to combine, so
//
//     b     = f(0)
//     col_j = f(e_j) ^ b
//
// recovers M and b from n+1 evaluations of the cut, identically for both
// semirings, instead of asking the solver to search a width^2-bit constant.
// The semirings differ only on inputs with more than one bit set, which is what
// the screen in extractABM decides. This is what makes wide XOR affordable:
// the cost no longer depends on the width of the matrix.
// ---------------------------------------------------------------------------

// Reference semantics, mirroring ABM_impl in alive2's smt/expr.cpp:
// out[n-1-i] = OP over j of M[i*n + j] & x[j], then ^ b.
static llvm::APInt abmApply(const llvm::APInt &X, const llvm::APInt &M,
                            const llvm::APInt &B, unsigned N, bool Exclusive) {
  llvm::APInt Res(N, 0);
  for (unsigned i = 0; i < N; ++i) {
    bool Acc = false;
    for (unsigned j = 0; j < N; ++j) {
      if (!M[uint64_t(i) * N + j] || !X[j])
        continue;
      if (!Exclusive) {
        Acc = true;
        break;
      }
      Acc = !Acc;
    }
    if (Acc)
      Res.setBit(N - 1 - i);
  }
  return Res ^ B;
}

// Fold a straight-line cut to a constant for one concrete input. Anything that
// does not constant fold (a load, a call with no folding rule, a phi) makes
// this bail out, and the caller falls back to ordinary enumeration.
static llvm::Constant *evalCut(llvm::Instruction *Root, llvm::Argument *X,
                               llvm::Constant *XVal,
                               const llvm::DataLayout &DL) {
  llvm::DenseMap<const llvm::Value *, llvm::Constant *> Env;
  Env[X] = XVal;

  for (auto &I : *Root->getParent()) {
    if (I.isTerminator())
      continue;
    if (llvm::isa<llvm::PHINode>(&I))
      return nullptr;

    llvm::SmallVector<llvm::Constant *, 8> Ops;
    bool Ready = true;
    for (auto &Op : I.operands()) {
      llvm::Constant *C = nullptr;
      if (auto *KC = llvm::dyn_cast<llvm::Constant>(Op.get())) {
        C = KC;
      } else {
        auto It = Env.find(Op.get());
        if (It != Env.end())
          C = It->second;
      }
      if (!C) {
        Ready = false;
        break;
      }
      Ops.push_back(C);
    }
    // an instruction the root does not depend on may legitimately be unfoldable
    if (!Ready) {
      if (&I == Root)
        return nullptr;
      continue;
    }

    // @ABM and @ABMX are our own intrinsics and have no LLVM folding rule. A
    // cut formed after an earlier rewrite can already contain them, which is
    // exactly the case for the composite ones worth extracting, so fold them
    // here rather than giving up.
    llvm::Constant *R = nullptr;
    if (auto *CI = llvm::dyn_cast<llvm::CallInst>(&I)) {
      auto *Callee = CI->getCalledFunction();
      if (Callee && Ops.size() == 4 &&
          (Callee->getName() == "ABM" || Callee->getName() == "ABMX")) {
        auto *XC = llvm::dyn_cast<llvm::ConstantInt>(Ops[0]);
        auto *MC = llvm::dyn_cast<llvm::ConstantInt>(Ops[1]);
        auto *BC = llvm::dyn_cast<llvm::ConstantInt>(Ops[2]);
        if (XC && MC && BC)
          R = llvm::ConstantInt::get(
              CI->getType(),
              abmApply(XC->getValue(), MC->getValue(), BC->getValue(),
                       XC->getBitWidth(), Callee->getName() == "ABMX"));
      }
    }
    if (!R)
      R = llvm::ConstantFoldInstOperands(&I, Ops, DL);
    if (!R) {
      if (&I == Root)
        return nullptr;
      continue;
    }
    Env[&I] = R;
    if (&I == Root)
      return R;
  }
  return nullptr;
}

// Recover M and b, then screen both semirings on inputs with many bits set.
// Returns the semirings that reproduced the cut on every sample.
static vector<ABM::Op> extractABM(llvm::Instruction *Root, llvm::Argument *X,
                                  unsigned N, const llvm::DataLayout &DL,
                                  llvm::APInt &M, llvm::APInt &B,
                                  unsigned &Samples) {
  vector<ABM::Op> Ops;
  Samples = 0;

  auto eval = [&](const llvm::APInt &V) -> llvm::ConstantInt * {
    auto *C = evalCut(Root, X, llvm::ConstantInt::get(X->getType(), V), DL);
    return C ? llvm::dyn_cast<llvm::ConstantInt>(C) : nullptr;
  };

  auto *Zero = eval(llvm::APInt(N, 0));
  if (!Zero)
    return Ops;
  B = Zero->getValue();

  M = llvm::APInt(uint64_t(N) * N, 0);
  for (unsigned j = 0; j < N; ++j) {
    llvm::APInt E(N, 0);
    E.setBit(j);
    auto *Cj = eval(E);
    if (!Cj)
      return Ops;
    llvm::APInt Col = Cj->getValue() ^ B;
    for (unsigned o = 0; o < N; ++o)
      if (Col[o])
        M.setBit(uint64_t(N - 1 - o) * N + j);
  }

  // fixed seed so a run is reproducible
  std::mt19937_64 RNG(0x9E3779B97F4A7C15ULL);
  bool OkOr = true, OkXor = true;
  for (unsigned k = 0; k < 256 && (OkOr || OkXor); ++k) {
    llvm::APInt V(N, 0);
    if (k == 0) {
      V.setAllBits();
    } else {
      for (unsigned w = 0; w < N; w += 64) {
        unsigned Chunk = std::min(64u, N - w);
        // mask explicitly: APInt(bits, val) does not truncate val for us, and
        // an over-wide word silently breaks the APInt's invariants
        uint64_t Word = RNG();
        if (Chunk < 64)
          Word &= (uint64_t(1) << Chunk) - 1;
        V.insertBits(llvm::APInt(Chunk, Word), w);
      }
    }
    auto *R = eval(V);
    if (!R)
      return {};
    if (OkOr && abmApply(V, M, B, N, false) != R->getValue())
      OkOr = false;
    if (OkXor && abmApply(V, M, B, N, true) != R->getValue())
      OkXor = false;
    ++Samples;
  }

  if (OkOr)
    Ops.push_back(ABM::OR);
  if (OkXor)
    Ops.push_back(ABM::XOR);
  return Ops;
}

vector<Rewrite> Enumerator::solve(llvm::Function &F, llvm::Instruction *I) {
  unsigned CANDIDATES = 0, PRUNED = 0, GOOD = 0;
  vector<Rewrite> ret;

  debug() << "[enumerator] working on slice\n" << F << "\n";

  clock_t start = std::clock();

  llvm::DominatorTree DT(F);
  DT.recalculate(F);

  std::unordered_set<llvm::Function *> IntrinsicDecls;

  unsigned src_cost = get_approx_cost(&F);

  llvm::DataLayout DL = F.getParent()->getDataLayout();

  llvm::Triple Triple = llvm::Triple(F.getParent()->getTargetTriple());
  llvm::TargetLibraryInfoWrapperPass TLI(Triple);

  // with machine cost ignored, both sides are compared in approx-cost units,
  // and the clang + llvm-mca round trip per slice is skipped entirely
  unsigned costBefore = config::ignore_machine_cost ? src_cost
                                                    : get_machine_cost(&F);

  unsigned Width = I->getType()->getScalarSizeInBits();
  llvm::KnownBits KnownI(Width);
  if (I->getType()->isIntOrIntVectorTy())
    computeKnownBits(I, KnownI, DL);

  findInputs(F, I, DT);

  vector<Sketch> Sketches;

  // immediate constant synthesis
  {
    set<ReservedConst*> RCs;
    auto RC = make_unique<ReservedConst>(type(I->getType()));
    auto CI = make_unique<Copy>(*RC.get());
    RCs.insert(RC.get());
    Sketches.push_back(make_pair(CI.get(), std::move(RCs)));
    exprs.emplace_back(std::move(CI));
    exprs.emplace_back(std::move(RC));
  }
  // nops
  {
    for (auto &V : values) {
      if (V->getType().getWidth() != I->getType()->getPrimitiveSizeInBits())
        continue;
      set<ReservedConst*> RCs;
      auto VA = make_unique<Var>(V->V());
      Sketches.push_back(make_pair(VA.get(), std::move(RCs)));
      exprs.emplace_back(std::move(VA));
    }
  }

  getSketches(&*I, Sketches);

  // Basis-vector extraction, tried ahead of the enumerated sketches. It costs
  // n+1 constant folds rather than an SMT search, so it is not width capped;
  // when the cut is not ABM shaped it simply adds nothing.
  if (config::abm_extract && config::abm_mode &&
      I->getType()->isIntegerTy()) {
    unsigned N = I->getType()->getIntegerBitWidth();

    // Exactly one used integer argument of the root's width: the unary
    // instruction class ABM is meant to describe.
    llvm::Argument *X = nullptr;
    unsigned Used = 0;
    for (auto &A : F.args()) {
      if (A.use_empty())
        continue;
      ++Used;
      if (A.getType()->isIntegerTy() &&
          A.getType()->getIntegerBitWidth() == N)
        X = &A;
    }

    if (Used == 1 && X && N > 1 && I->getParent() == &F.getEntryBlock() &&
        uint64_t(N) * N <= std::numeric_limits<unsigned>::max()) {
      llvm::APInt MV(1, 0), BV(1, 0);
      unsigned Samples = 0;
      auto Ops = extractABM(&*I, X, N, DL, MV, BV, Samples);

      for (auto Op : Ops) {
        type matTy = type::Integer((unsigned)(uint64_t(N) * N));
        auto *MC = llvm::ConstantInt::get(matTy.toLLVM(F.getContext()), MV);
        auto *BC = llvm::ConstantInt::get(I->getType(), BV);

        auto XT = make_unique<Var>(X);
        auto MT = make_unique<ReservedConst>(matTy, MC);
        auto BT = make_unique<ReservedConst>(type(I->getType()), BC);
        auto AT = make_unique<ABM>(*XT.get(), *MT.get(), *BT.get(), Op);

        debug() << "[enumerator] extracted " << *AT.get() << " from "
                << Samples << " samples\n";

        // Log the extraction itself, not just whatever survives verification.
        // Wide XOR is often beyond the solver's budget even though the matrix
        // is computed exactly, and losing those is losing the best findings.
        if (!config::abm_log_path.empty()) {
          // known bits are a property of the original value, not of a cut
          // argument, so extraction records leave them empty
          vector<ABMUse> U{ABMUse{N, MV, BV, true, Op == ABM::XOR, 0, 1.0,
                                  llvm::APInt(N, 0), llvm::APInt(N, 0)}};
          string cutIR;
          llvm::raw_string_ostream cs(cutIR);
          cs << F;
          cs.flush();
          writeABMRecords(U, "extracted", cutIR, src_cost, 1,
                          "basis-vectors+" + std::to_string(Samples) +
                              "-samples");
        }

        // no reserved constants: HaveC stays false and the candidate goes
        // straight to verification instead of constant synthesis
        Sketches.insert(Sketches.begin(),
                        make_pair(AT.get(), set<ReservedConst*>()));
        exprs.emplace_back(std::move(AT));
        exprs.emplace_back(std::move(BT));
        exprs.emplace_back(std::move(MT));
        exprs.emplace_back(std::move(XT));
      }
    }
  }

  debug() << "[enumerator] listing sketches\n";
  for (auto &Sketch : Sketches) {
    debug() << *Sketch.first << "\n";
  }

  unsigned CI = 0;

  vector<Candidate> Fns;
  auto FT = F.getFunctionType();
  // sketches -> llvm functions

  for (auto &Sketch : Sketches) {
    bool HaveC = !Sketch.second.empty();
    auto &G = Sketch.first;
    llvm::ValueToValueMapTy VMap;

    llvm::SmallVector<llvm::Type*, 8> Args;
    for (auto A: FT->params()) {
      Args.push_back(A);
    }
    for (auto &C : Sketch.second) {
      Args.push_back(C->getType().toLLVM(F.getContext()));
    }

    auto _functionType =
      llvm::FunctionType::get(FT->getReturnType(), Args, FT->isVarArg());

    llvm::Function *Tgt =
      llvm::Function::Create(_functionType, F.getLinkage(),
                              F.getName(), F.getParent());


    llvm::Function::arg_iterator TgtArgI = Tgt->arg_begin();

    for (auto A = F.arg_begin(), E = F.arg_end(); A != E; ++A, ++TgtArgI) {
      VMap[A] = TgtArgI;
      TgtArgI->setName(A->getName());
    }

    unordered_map<const llvm::Argument*, ReservedConst*> ArgConst;
    // sketches with constants, duplicate F
    for (auto &C : Sketch.second) {
      string arg_name = "_reservedc_" + std::to_string(CI);
      TgtArgI->setName(arg_name);
      C->setA(TgtArgI);
      ArgConst[TgtArgI] = C;
      ++CI;
      ++TgtArgI;
    }

    llvm::SmallVector<llvm::ReturnInst*, 8> _returns;
    llvm::CloneFunctionInto(Tgt, &F, VMap,
      llvm::CloneFunctionChangeType::LocalChangesOnly, _returns);

    llvm::Function *Src;
    if (HaveC) {
      llvm::ValueToValueMapTy _vs;
      Src = llvm::CloneFunction(Tgt, _vs);
    } else {
      Src = &F;
    }

    llvm::Instruction *PrevI = llvm::cast<llvm::Instruction>(VMap[&*I]);
    llvm::Value *V =
        LLVMGen(PrevI, IntrinsicDecls).codeGen(G, VMap);
    V = llvm::IRBuilder<>(PrevI).CreateBitCast(V, PrevI->getType());
    PrevI->replaceAllUsesWith(V);

    eliminate_dead_code(*Tgt);
    unsigned tgt_cost = get_approx_cost(Tgt);

    ++CANDIDATES;

    bool skip = false;
    string err;
    llvm::raw_string_ostream err_stream(err);
    bool illformed = llvm::verifyFunction(*Tgt, &err_stream);
    llvm::KnownBits KnownV(Width);


    // TODO: add more pruning here
    if (illformed) {
      llvm::errs() << "Error tgt found: " << err << "\n";
      Tgt->dump();
      skip = true;
      goto push;
    }

    // check cost
    if (tgt_cost >= src_cost) {
      skip = true;
      goto push;
    }
push:
    if (skip) {
      Tgt->eraseFromParent();
      if (HaveC)
        Src->eraseFromParent();
    } else {
      Fns.push_back(make_tuple(Tgt, Src, G, ArgConst, !Sketch.second.empty()));
    }
  }
  std::stable_sort(Fns.begin(), Fns.end(), approx);
  // llvm functions -> alive2 functions
  auto iter = Fns.begin();

  for (;iter != Fns.end();) {
    auto &[Tgt, Src, G, ArgConst, HaveC] = *iter;
    unsigned tgt_cost = get_approx_cost(Tgt);
    debug() << "[enumerator] approx_cost(tgt) = " << tgt_cost
            << ", approx_cost(src) = " << src_cost <<"\n";
    debug() << *Tgt;

    bool Good = false;
    unordered_map<llvm::Argument*, llvm::Constant*> ConstantResults;

    try {
      if (!HaveC) {
        AliveEngine AE(TLI);
        Good = AE.compareFunctions(*Src, *Tgt);
      } else {
        AliveEngine AE(TLI);
        Good = AE.constantSynthesis(*Src, *Tgt, ConstantResults);
      }
    } catch (const AliveException &E) {
      debug() << E.msg << "\n";
      // "slow vcgen" means the candidate is too complex to verify quickly;
      // fall through to cleanup and try the next candidate.
    }
    if (Good) {
      GOOD ++;
      Inst *R = G;
      if (HaveC) {
        for (auto &[A, C] : ConstantResults) {
          //Consts[ArgConst[A]] = C;
          ArgConst[A]->setC(C);
          A->replaceAllUsesWith(C);
        }
      }

      // rewrite fksv calls to shufflevector
      for (auto &BB : *Tgt) {
        for (auto &I : make_early_inc_range(BB)) {
          if (!isa<llvm::CallInst>(&I))
            continue;
          auto CI = llvm::cast<llvm::CallInst>(&I);

          auto callee = CI->getCalledFunction();
          if (!callee)
            continue;
          if (!callee->getName().starts_with("__fksv"))
            continue;

          auto shuf = new llvm::ShuffleVectorInst(CI->getArgOperand(0),
                                                  CI->getArgOperand(1),
                                                  CI->getArgOperand(2), "",
                                                  CI->getIterator());
          CI->replaceAllUsesWith(shuf);
          CI->eraseFromParent();
        }
      }

      unsigned costAfter = config::ignore_machine_cost
                             ? tgt_cost
                             : get_machine_cost(Tgt);

      debug() << "[enumerator] optimized ir (uops=" << costAfter <<")"
              << ", original cost (uops=" << costBefore << "), \n"
              << *Tgt << "\n";

      // a zero machine cost means llvm-mca failed; an approx cost of zero is
      // a legitimate constant rewrite
      if (!config::ignore_machine_cost && (!costAfter || !costBefore)) {
        debug() << "[enumerator] cost is zero, skip\n";
      } else if (config::ignore_machine_cost || costAfter < costBefore) {
        debug () << "[enumerator] successfully synthesized rhs\n";
        ret.emplace_back(R, costAfter, costBefore);
        // harvest ABM matrices now; Tgt is erased at the end of this iteration
        if (config::abm_log_all && !config::abm_log_path.empty())
          collectABMUses(*Tgt, ret.back().ABMs);
      } else {
        debug() << "[enumerator] successfully synthesized rhs, "
                << "however, rhs is more expensive than lhs\n";
      }
    }

    if (HaveC)
      Src->eraseFromParent();
    Tgt->eraseFromParent();

    iter = Fns.erase(iter);

    unsigned Duration = ( std::clock() - start ) / CLOCKS_PER_SEC;
    if ((config::return_first_solution && Good)) {
      debug() << "[enumerator] returning first solution\n";
      break;
    }
    if (Duration > config::slice_to) {
      debug() << "[enumerator] timeout for candidate, skipping\n";
      break;
    }
  }

  for (;iter != Fns.end(); ++iter) {
    auto &[Tgt, Src, _, __, HaveC] = *iter;
    if (HaveC)
      Src->eraseFromParent();
    Tgt->eraseFromParent();
  }

  debug() << "[enumerator] #Candidates = "<< CANDIDATES
          << ", #Pruned = " << PRUNED
          << ", #Good = " << GOOD << "\n";

  std::stable_sort(ret.begin(), ret.end(),
    [](const Rewrite &a, const Rewrite &b) {
      return a.CostAfter < b.CostAfter;
    });

  for (auto &R : ret) {
    debug() << "[enumerator] rewrite: " << *R.I
            << ", cost="<<  R.CostAfter << "\n";
  }

  // Log the verified ABM rewrites for this cut. ret[0] is the one that will be
  // applied; it is logged from the finished function in optimize_function
  // instead, so that superseded intermediates don't pollute the ranking.
  if (config::abm_log_all && !config::abm_log_path.empty()) {
    string cutIR;
    llvm::raw_string_ostream cs(cutIR);
    cs << F;
    cs.flush();
    for (auto &R : ret)
      writeABMRecords(R.ABMs, "candidate", cutIR, R.CostBefore, R.CostAfter);
  }

  removeUnusedDecls(IntrinsicDecls);
  return ret;
}

};
