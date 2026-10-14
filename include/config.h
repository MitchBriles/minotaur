// Copyright (c) 2020-present, author: Zhengyang Liu (liuz@cs.utah.edu).
// Distributed under the MIT license that can be found in the LICENSE file.
#pragma once

#include "llvm/Support/raw_ostream.h"

#include <string>

namespace minotaur {
namespace config {

extern bool disable_poison_input;
extern bool disable_undef_input;
extern bool debug_slicer;
extern bool debug_enumerator;
extern bool debug_tv;
extern bool debug_codegen;
extern bool debug_parser;
extern bool ignore_machine_cost;
extern bool smt_verbose;
extern bool disable_avx512;
extern bool show_stats;
extern bool return_first_solution;

extern unsigned slice_to;
extern unsigned slicer_max_depth;

// Widest scalar ABM is enumerated for. The matrix is a width^2-bit constant
// that has to be synthesized, so cost grows quickly with width; lower this
// if synthesis is too slow on wide cuts.
extern unsigned abm_max_width;

// Same for the XOR semiring (@ABMX).
extern unsigned abm_xor_max_width;

// Approximate cost of one ABM call; ordinary instructions cost 2. At the
// default 1 an ABM beats any single instruction; 5 means it has to replace
// about three of them.
extern unsigned abm_cost;

// Recover the ABM matrix by evaluating the cut on basis vectors instead of
// asking the solver to search for it. Cheap and not width limited; turn off to
// compare against pure enumeration.
extern bool abm_extract;

// Which semirings the enumerator considers: 1 = OR (@ABM), 2 = XOR (@ABMX),
// 3 = both.
extern unsigned abm_mode;
enum { ABM_MODE_OR = 1, ABM_MODE_XOR = 2, ABM_MODE_BOTH = 3 };

// ABM matrix logging; see scripts/abm-rank.py
extern std::string abm_log_path;
extern bool abm_log_all;
extern std::string current_function;
extern std::string current_source;

llvm::raw_ostream &dbg();
void set_debug(llvm::raw_ostream &os);

extern const char minotaur_version[];

} // namespace config
} // namespace minotaur
