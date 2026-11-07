#include "kernel.h"
void *memset(void *dst, int value, size_t count)
{
    unsigned char *p = dst;
    while (count--) *p++ = (unsigned char)value;
    return dst;
}
void *memcpy(void *dst, const void *src, size_t count)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    while (count--) *d++ = *s++;
    return dst;
}
int memcmp(const void *a, const void *b, size_t count)
{
    const unsigned char *x = a, *y = b;
    while (count--) { if (*x != *y) return (int)*x - *y; ++x; ++y; }
    return 0;
}
size_t strlen(const char *s) { size_t n = 0; while (s[n]) ++n; return n; }
int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) { ++a; ++b; }
    return (unsigned char)*a - (unsigned char)*b;
}
