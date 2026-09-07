; multiply by 2 -- same transform as shl1.ll and addself.ll
define i8 @mul2(i8 %x) {
  %r = mul i8 %x, 2
  ret i8 %r
}
