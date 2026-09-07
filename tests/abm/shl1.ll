; shl by 1 -- same transform as mul2.ll and addself.ll
define i8 @shl1(i8 %x) {
  %r = shl i8 %x, 1
  ret i8 %r
}
