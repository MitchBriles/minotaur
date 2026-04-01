; @BIG_vec = constant <11 x i11> <
;   i11 2,
;   i11 4,
;   i11 8,
;   i11 16,
;   i11 32,
;   i11 64,
;   i11 128,
;   i11 256,
;   i11 512,
;   i11 1024,
;   i11 0
; >

define i11 @src(i11 %x) {
  %a = call i11 @llvm.bitreverse.i11 (i11 %x)
  %b = mul i11 2, %a 
  %res = add i11 %b, 1
  ret i11 %res
}

; define i11 @tgt(i11 %x) {
;   %ptr = bitcast <11 x i11>* @BIG_vec to i121*
;   %M = load i121, i121* %ptr, align 16
;   %res = call i11 @ABM(i11 %x, i121 %M, i11 1)
;   ret i11 %res
; }

declare i11 @ABM(i11, i121, i11)
