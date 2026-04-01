; ModuleID = 'tests/sh4.ll'
source_filename = "tests/sh4.ll"

define i8 @sh4(i8 %0) {
  %2 = call i8 @ABM(i8 %0, i64 9078117754732544, i8 0)
  ret i8 %2
}

; Function Attrs: nounwind willreturn memory(none)
declare i8 @ABM(i8, i64, i8) #0

attributes #0 = { nounwind willreturn memory(none) }
