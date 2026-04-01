; ModuleID = 'tests/ABM_rev_11.ll'
source_filename = "tests/ABM_rev_11.ll"

define i11 @src(i11 %x) {
  %1 = call i11 @ABM(i11 %x, i121 649195602336955249281468649709570, i11 1)
  ret i11 %1
}

; Function Attrs: nounwind willreturn memory(none)
declare i11 @ABM(i11, i121, i11) #0

; Function Attrs: nocallback nocreateundeforpoison nofree nosync nounwind speculatable willreturn memory(none)
declare i11 @llvm.bitreverse.i11(i11) #1

attributes #0 = { nounwind willreturn memory(none) }
attributes #1 = { nocallback nocreateundeforpoison nofree nosync nounwind speculatable willreturn memory(none) }
