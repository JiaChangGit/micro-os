#include "trap.h"
.section .text.vectors,"ax"
.balign 2048
.global vectors
vectors:
.macro SLOT target
    b \target
    .balign 128
.endm
    SLOT sync_entry
    SLOT irq_entry
    SLOT bad_entry
    SLOT bad_entry
    SLOT sync_entry
    SLOT irq_entry
    SLOT bad_entry
    SLOT bad_entry
    SLOT sync_entry
    SLOT irq_entry
    SLOT bad_entry
    SLOT bad_entry
    SLOT bad_entry
    SLOT bad_entry
    SLOT bad_entry
    SLOT bad_entry

.macro SAVE kind
    sub sp, sp, #TF_SIZE
    stp x0, x1, [sp, #0]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x9, [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x19, [sp, #144]
    stp x20, x21, [sp, #160]
    stp x22, x23, [sp, #176]
    stp x24, x25, [sp, #192]
    stp x26, x27, [sp, #208]
    stp x28, x29, [sp, #224]
    str x30, [sp, #240]
    mrs x0, sp_el0
    mrs x1, elr_el1
    stp x0, x1, [sp, #TF_SP]
    mrs x0, spsr_el1
    mrs x1, esr_el1
    stp x0, x1, [sp, #TF_SPSR]
    mrs x0, far_el1
    str x0, [sp, #TF_FAR]
    mov x0, sp
    mov x1, #\kind
    bl exception_dispatch
    mov sp, x0
    b trap_restore
.endm
sync_entry:
    SAVE 0
irq_entry:
    SAVE 1
bad_entry:
    SAVE 2

.global trap_restore
trap_restore:
    ldp x0, x1, [sp, #TF_SP]
    msr sp_el0, x0
    msr elr_el1, x1
    ldr x0, [sp, #TF_SPSR]
    msr spsr_el1, x0
    ldp x0, x1, [sp, #0]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x9, [sp, #64]
    ldp x10, x11, [sp, #80]
    ldp x12, x13, [sp, #96]
    ldp x14, x15, [sp, #112]
    ldp x16, x17, [sp, #128]
    ldp x18, x19, [sp, #144]
    ldp x20, x21, [sp, #160]
    ldp x22, x23, [sp, #176]
    ldp x24, x25, [sp, #192]
    ldp x26, x27, [sp, #208]
    ldp x28, x29, [sp, #224]
    ldr x30, [sp, #240]
    add sp, sp, #TF_SIZE
    eret

.global vm_brk_test
vm_brk_test:
    brk #0x123
    ret
.global vm_high_probe
vm_high_probe:
    adr x0, vm_high_probe
    ret
.global enter_frame
enter_frame:
    mov sp, x0
    b trap_restore
