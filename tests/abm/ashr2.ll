; arithmetic shift by a constant is GF(2)-linear: the replicated sign bits are
; all copies of x[7]
define i8 @ashr2(i8 %x) {
  %r = ashr i8 %x, 2
  ret i8 %r
}
