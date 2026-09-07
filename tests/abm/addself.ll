; x + x -- same transform as shl1.ll and mul2.ll
define i8 @addself(i8 %x) {
  %r = add i8 %x, %x
  ret i8 %r
}
