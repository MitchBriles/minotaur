define i8 @sh4(i8 %0) {
    %2 = lshr i8 %0, 5
    %3 = shl i8 %2, 1
    ret i8 %3
}

