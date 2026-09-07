; carry-less multiply by the constant 3, i.e. x ^ (x << 1).
; Like xorshift.ll, the XOR ends up in the mask operand rather than the matrix.
define i8 @clmul3(i8 %x) {
  %s = shl i8 %x, 1
  %r = xor i8 %x, %s
  ret i8 %r
}
