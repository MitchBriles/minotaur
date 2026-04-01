@BIG_vec = constant <32 x i32> <
  i32 33554432,
  i32 16777216,
  i32 8388608,
  i32 4194304,
  i32 2097152,
  i32 1048576,
  i32 524288,
  i32 262144,
  i32 131072,
  i32 65536,
  i32 32768,
  i32 16384,
  i32 8192,
  i32 4096,
  i32 2048,
  i32 1024,
  i32 512,
  i32 256,
  i32 128,
  i32 64,
  i32 32,
  i32 16,
  i32 8,
  i32 4,
  i32 2,
  i32 1,
  i32 2147483648,
  i32 1073741824,
  i32 536870912,
  i32 268435456,
  i32 134217728,
  i32 67108864
>

define i32 @src(i32 %x) {
  %res = call i32 @llvm.fshl.32 (i32 %x, i32 %x, i32 6)
  ret i32 %res
}

; define i32 @tgt(i32 %x) {
;   %ptr = bitcast <32 x i32>* @BIG_vec to i1024*
;   %M = load i1024, i1024* %ptr, align 16
;   %res = call i32 @ABM(i32 %x, i1024 %M, i32 0)
;   ret i32 %res
; }

declare i64 @ABM(i32, i1024, i32)
