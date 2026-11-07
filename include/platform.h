#ifndef MICRO_OS_PLATFORM_H
#define MICRO_OS_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

#define PLATFORM_DTB_MAX (2u * 1024u * 1024u)

struct platform {
    uint64_t ram_base, ram_size;
    uint64_t uart_base, rtc_base;
    uint64_t gicd_base, gicr_base;
    uint32_t rtc_irq, timer_irq;
    uint64_t dtb_size;
    char bootargs[64];
};

/* 成功為 0；格式或未支援的裝置拓撲為 -1，失敗不修改 out。 */
int platform_parse(const void *blob, size_t available, struct platform *out);

#endif
