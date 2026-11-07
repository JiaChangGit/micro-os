#ifndef MICRO_KERNEL_H
#define MICRO_KERNEL_H
#include <stddef.h>
#include <stdint.h>
#include "platform.h"

extern struct platform plat;
extern uint64_t boot_fdt;
void kprintf(const char *format, ...) __attribute__((format(printf, 1, 2)));
void console_init(uint64_t address);
_Noreturn void test_exit(unsigned status);
_Noreturn void panic(const char *message);
void *memset(void *dst, int value, size_t count);
void *memcpy(void *dst, const void *src, size_t count);
int memcmp(const void *a, const void *b, size_t count);
size_t strlen(const char *s);
int strcmp(const char *a, const char *b);
static inline uint32_t mmio_read(uint64_t address) { return *(volatile uint32_t *)(uintptr_t)address; }
static inline void mmio_write(uint64_t address, uint32_t value) { *(volatile uint32_t *)(uintptr_t)address = value; }
static inline void barrier(void) { __asm__ volatile("dsb sy; isb" ::: "memory"); }
#endif
