#include "uapi.h"
.section .text.start, "ax"
.global _start
.type _start, %function
_start:
    bl user_main
    mov x8, #UAPI_SYS_EXIT
    mov x0, #0
    svc #0
1:  b 1b
.size _start, . - _start
.section .note.GNU-stack, "", %progbits
