; Under OR accumulation the matrix cannot express the XOR, so the synthesizer
; routes it through the mask operand instead: ABM(x, M_lshr3, %x). The record
; this produces has a runtime mask, so its matrix describes only the shift.
define i8 @xorshift(i8 %x) {
  %s = lshr i8 %x, 3
  %r = xor i8 %x, %s
  ret i8 %r
}
