#include "kernel.h"
#include "irq.h"

enum {
    GIC_CTLR = 0x0000, GIC_TYPER = 0x0004, GIC_GROUP = 0x0080,
    GIC_ENABLE = 0x0100, GIC_DISABLE = 0x0180,
    GIC_PENDING_CLEAR = 0x0280, GIC_ACTIVE_CLEAR = 0x0380,
    GIC_PRIORITY = 0x0400, GIC_CONFIG = 0x0c00,
    GIC_ROUTE = 0x6000, GIC_PIDR2 = 0xffe8, GICR_WAKER = 0x0014,
};

static uint64_t distributor, redistributor, private_bank;
static uint32_t irq_count;

static void wait_clear(uint64_t address, uint32_t mask)
{
    for (unsigned attempt = 0; attempt < 1000000; ++attempt) {
        if (!(mmio_read(address) & mask)) return;
        __asm__ volatile("yield");
    }
    panic("GIC register wait");
}

static void dist_sync(void)
{
    wait_clear(distributor + GIC_CTLR, 1u << 31);
}

static void redist_sync(void)
{
    wait_clear(redistributor + GIC_CTLR, 1u << 3);
}

static uint64_t bank_for(uint32_t id)
{
    if (id < 16 || id >= irq_count) panic("IRQ number");
    return id < 32 ? private_bank : distributor;
}

void irq_enable(uint32_t id)
{
    uint64_t bank = bank_for(id);
    barrier();
    mmio_write(bank + GIC_ENABLE + (id / 32) * 4, 1u << (id % 32));
    if (id < 32) redist_sync(); else dist_sync();
    barrier();
}

void irq_disable(uint32_t id)
{
    uint64_t bank = bank_for(id);
    mmio_write(bank + GIC_DISABLE + (id / 32) * 4, 1u << (id % 32));
    if (id < 32) redist_sync(); else dist_sync();
    barrier();
}

uint32_t irq_acknowledge(void)
{
    uint64_t id;
    /* IAR 會改變 active state，只在 IRQ handler 讀一次。 */
    __asm__ volatile("mrs %0, ICC_IAR1_EL1; dsb sy" : "=r"(id) :: "memory");
    return (uint32_t)(id & 0xffffff);
}

void irq_eoi(uint32_t id)
{
    if (id >= 1020) return;
    /* EOImode=0，這次寫入同時 drop priority 與 deactivate。 */
    __asm__ volatile("dsb sy; msr ICC_EOIR1_EL1, %0; isb" :: "r"((uint64_t)id) : "memory");
}

uint64_t timer_now(void)
{
    uint64_t count;
    __asm__ volatile("isb; mrs %0, CNTPCT_EL0" : "=r"(count) :: "memory");
    return count;
}

uint64_t timer_frequency(void)
{
    uint64_t frequency;
    __asm__ volatile("mrs %0, CNTFRQ_EL0" : "=r"(frequency));
    return frequency;
}

void timer_set(uint64_t deadline)
{
    __asm__ volatile("msr CNTP_CVAL_EL0, %0; msr CNTP_CTL_EL0, %1; isb"
                     :: "r"(deadline), "r"(1ull) : "memory");
}

void timer_stop(void)
{
    __asm__ volatile("msr CNTP_CTL_EL0, xzr; isb" ::: "memory");
}

void irq_init(const struct platform *platform)
{
    distributor = platform->gicd_base;
    redistributor = platform->gicr_base;
    private_bank = redistributor + 0x10000;
    if ((mmio_read(distributor + GIC_PIDR2) & 0xf0) != 0x30 ||
        (mmio_read(redistributor + GIC_PIDR2) & 0xf0) != 0x30)
        panic("GICv3 identification");
    irq_count = ((mmio_read(distributor + GIC_TYPER) & 31) + 1) * 32;
    if (irq_count > 1020) irq_count = 1020;
    if (platform->timer_irq < 16 || platform->timer_irq >= 32 ||
        platform->rtc_irq < 32 || platform->rtc_irq >= irq_count)
        panic("FDT IRQ range");

    timer_stop();
    mmio_write(distributor + GIC_CTLR, 0);
    dist_sync();
    for (unsigned id = 32; id < irq_count; id += 32) {
        mmio_write(distributor + GIC_DISABLE + id / 8, UINT32_MAX);
        mmio_write(distributor + GIC_ACTIVE_CLEAR + id / 8, UINT32_MAX);
        mmio_write(distributor + GIC_PENDING_CLEAR + id / 8, UINT32_MAX);
        mmio_write(distributor + GIC_GROUP + id / 8, UINT32_MAX);
    }
    for (unsigned id = 32; id < irq_count; id += 16)
        mmio_write(distributor + GIC_CONFIG + id / 4, 0);
    for (unsigned id = 32; id < irq_count; id += 4)
        mmio_write(distributor + GIC_PRIORITY + id, 0xa0a0a0a0);
    dist_sync();
    /* ARE_NS 加 Group 1 enable；支援 QEMU 的單 security-state 視圖。 */
    mmio_write(distributor + GIC_CTLR, 0x13);
    dist_sync();
    uint64_t mpidr;
    __asm__ volatile("mrs %0, MPIDR_EL1" : "=r"(mpidr));
    uint64_t affinity = (mpidr & 0xff00ffffffull);
    for (unsigned id = 32; id < irq_count; ++id)
        *(volatile uint64_t *)(uintptr_t)(distributor + GIC_ROUTE + id * 8) = affinity;

    uint32_t wake = mmio_read(redistributor + GICR_WAKER);
    mmio_write(redistributor + GICR_WAKER, wake & ~(1u << 1));
    wait_clear(redistributor + GICR_WAKER, 1u << 2);
    mmio_write(private_bank + GIC_DISABLE, UINT32_MAX);
    mmio_write(private_bank + GIC_ACTIVE_CLEAR, UINT32_MAX);
    mmio_write(private_bank + GIC_PENDING_CLEAR, UINT32_MAX);
    mmio_write(private_bank + GIC_GROUP, UINT32_MAX);
    for (unsigned id = 0; id < 32; id += 4)
        mmio_write(private_bank + GIC_PRIORITY + id, 0xa0a0a0a0);
    /* PPI 採 level；SGI 的固定 edge 屬性不修改。 */
    mmio_write(private_bank + GIC_CONFIG + 4, 0);
    redist_sync();

    uint64_t sre;
    __asm__ volatile("mrs %0, ICC_SRE_EL1" : "=r"(sre));
    sre |= 1;
    __asm__ volatile("msr ICC_SRE_EL1, %0; isb" :: "r"(sre) : "memory");
    __asm__ volatile("mrs %0, ICC_SRE_EL1" : "=r"(sre));
    if (!(sre & 1)) panic("GIC system registers unavailable");
    __asm__ volatile("msr ICC_CTLR_EL1, xzr; isb; msr ICC_BPR1_EL1, xzr; "
                     "msr ICC_PMR_EL1, %0; msr ICC_IGRPEN1_EL1, %1; isb"
                     :: "r"(0xffull), "r"(1ull) : "memory");
    if (!timer_frequency()) panic("Timer frequency");
    irq_enable(platform->timer_irq);
}
