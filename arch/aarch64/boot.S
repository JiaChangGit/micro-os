.section .text.header, "ax"
.global _start
_start:
    b boot_entry
    .long 0
    .quad 0x80000
    .quad __image_mem_size
    .quad 2
    .quad 0, 0, 0
    .ascii "ARM\x64"
    .long 0

.section .text.boot, "ax"
.global boot_entry
boot_entry:
    msr daifset, #15
    mov x20, x0
    ldr x1, =__bss_start
    ldr x2, =__bss_end
1:  cmp x1, x2
    b.hs 2f
    stp xzr, xzr, [x1], #16
    b 1b
2:  ldr x1, =boot_stack_top
    mov sp, x1
    mov x0, x20
    bl kernel_main
3:  wfi
    b 3b

.section .bss.stack, "aw", %nobits
.balign 4096
boot_stack:
    .skip 32768
.global boot_stack_top
boot_stack_top:
