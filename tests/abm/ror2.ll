define i8 @ror2(i8 %x) {
  %r = call i8 @llvm.fshr.i8(i8 %x, i8 %x, i8 2)
  ret i8 %r
}
declare i8 @llvm.fshr.i8(i8, i8, i8)
