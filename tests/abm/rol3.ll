define i8 @rol3(i8 %x) {
  %r = call i8 @llvm.fshl.i8(i8 %x, i8 %x, i8 3)
  ret i8 %r
}
declare i8 @llvm.fshl.i8(i8, i8, i8)
