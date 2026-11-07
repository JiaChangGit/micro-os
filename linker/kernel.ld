ENTRY(_start)
PHDRS {
    text PT_LOAD FLAGS(5);
    rodata PT_LOAD FLAGS(4);
    data PT_LOAD FLAGS(6);
}
SECTIONS {
    . = 0x40080000;
    __image_start = .;
    .text : { KEEP(*(.text.header)) *(.text.boot) *(.text .text.*) } :text
    . = ALIGN(4096);
    __text_end = .;
    .rodata : { *(.rodata .rodata.*) } :rodata
    . = ALIGN(4096);
    __rodata_end = .;
    .data : { *(.data .data.*) } :data
    . = ALIGN(4096);
    __bss_start = .;
    .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) } :data
    . = ALIGN(4096);
    __bss_end = .;
    __image_end = .;
    __image_mem_size = __image_end - __image_start;
    /DISCARD/ : { *(.comment .note.* .eh_frame*) }
}
ASSERT((_start == 0x40080000), "Image entry moved")
ASSERT((boot_entry - _start >= 64), "Image header overlap")
