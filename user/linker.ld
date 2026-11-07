ENTRY(_start)
PHDRS {
    text PT_LOAD FLAGS(5);
    rodata PT_LOAD FLAGS(4);
    data PT_LOAD FLAGS(6);
}
SECTIONS {
    . = 0x00400000;
    .text : { KEEP(*(.text.start)) *(.text .text.*) } :text
    . = ALIGN(4096);
    .rodata : { *(.rodata .rodata.*) } :rodata
    . = ALIGN(4096);
    .data : { *(.data .data.*) } :data
    .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) } :data
    . = ALIGN(4096);
    ASSERT(. <= 0x00600000, "user image exceeds fixed slot")
    /DISCARD/ : { *(.comment .note* .eh_frame*) }
}
