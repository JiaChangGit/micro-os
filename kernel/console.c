#include "kernel.h"
#include <stdarg.h>

static uint64_t uart;
void console_init(uint64_t address) { uart = address; }
static void putc(char c)
{
    while (mmio_read(uart + 0x18) & (1u << 5)) { }
    mmio_write(uart, (unsigned char)c);
}
static void number(uint64_t value, unsigned base)
{
    char digits[32];
    unsigned n = 0;
    do { digits[n++] = "0123456789abcdef"[value % base]; value /= base; } while (value);
    while (n) putc(digits[--n]);
}
void kprintf(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    while (*format) {
        if (*format++ != '%') { putc(format[-1]); continue; }
        int wide = *format == 'l';
        if (wide) ++format;
        char code = *format++;
        if (code == 's') {
            const char *s = va_arg(args, const char *);
            while (*s) putc(*s++);
        } else if (code == 'u' || code == 'x') {
            uint64_t value = wide ? va_arg(args, unsigned long) : va_arg(args, unsigned);
            number(value, code == 'x' ? 16 : 10);
        } else if (code == 'd') {
            int value = va_arg(args, int);
            if (value < 0) { putc('-'); number(-(int64_t)value, 10); }
            else number((unsigned)value, 10);
        } else putc(code);
    }
    va_end(args);
}
_Noreturn void test_exit(unsigned status)
{
    uint64_t block[] = {0x20026, status};
    register uint64_t operation __asm__("x0") = 0x20;
    register uint64_t argument __asm__("x1") = (uint64_t)(uintptr_t)block;
    __asm__ volatile("hlt #0xf000" : "+r"(operation), "+r"(argument) :: "memory");
    for (;;) __asm__ volatile("wfi");
}
_Noreturn void panic(const char *message)
{
    if (uart) kprintf("[PANIC] %s\n", message);
    test_exit(1);
}
