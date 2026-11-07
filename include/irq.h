#ifndef MICRO_OS_IRQ_H
#define MICRO_OS_IRQ_H

#include <stdint.h>
#include "platform.h"

/* 單 CPU、Group 1；初始化後 timer PPI 已啟用，RTC SPI 仍關閉。 */
void irq_init(const struct platform *platform);
void irq_enable(uint32_t id);
void irq_disable(uint32_t id);
uint32_t irq_acknowledge(void);
void irq_eoi(uint32_t id);

/* 時間域都是 CNTPCT ticks；timer_set 接受絕對 deadline。 */
uint64_t timer_now(void);
uint64_t timer_frequency(void);
void timer_set(uint64_t deadline);
void timer_stop(void);

#endif
