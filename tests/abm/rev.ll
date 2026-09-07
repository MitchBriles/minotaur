define i8 @rev(i8 %x) {
  %r = call i8 @llvm.bitreverse.i8(i8 %x)
  ret i8 %r
}
declare i8 @llvm.bitreverse.i8(i8)
