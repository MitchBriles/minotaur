; two-instruction sequence, in the style of tests/sh4.ll
define i8 @shr5shl1(i8 %x) {
  %a = lshr i8 %x, 5
  %r = shl i8 %a, 1
  ret i8 %r
}
