; CHECK: (fcmp_olt (var float %__n2) (reservedconst float |float f0x3F0943C8|) b1)
define i1 @src(float %0) {
  %t1 = fmul float %0, 0x3FF0CCCCC0000000
  %t2 = fcmp olt float %t1, 0x3FE20418A0000000
  ret i1 %t2
}