#include "kernel.h"
#include "trap.h"
#include "vm.h"
#include "task.h"

struct platform plat;
uint64_t boot_fdt;
void kernel_main(uint64_t fdt)
{
    uint64_t el;
    __asm__ volatile("mrs %0, CurrentEL" : "=r"(el));
    if (el != 4 || fdt < 0x40000000 || fdt > 0x60000000 - 0x200000)
        test_exit(1);
    if (platform_parse((const void *)(uintptr_t)fdt, 0x200000, &plat)) test_exit(2);
    boot_fdt = fdt;
    console_init(plat.uart_base);
    const char *test = plat.bootargs[0] ? plat.bootargs + 5 : "p7-demo";
    if (plat.bootargs[0] && memcmp(plat.bootargs, "case=", 5)) panic("invalid bootargs");
    kprintf("[TEST] CASE=%s START\n", test);
    kprintf("[BOOT] el=%u fdt=0x%lx size=%lu\n", (unsigned)(el >> 2), fdt, plat.dtb_size);
    kprintf("[PLATFORM] ram_base=0x%lx ram_size=0x%lx uart=0x%lx rtc=0x%lx gicd=0x%lx gicr=0x%lx rtc_irq=%u timer_irq=%u\n",
            plat.ram_base, plat.ram_size, plat.uart_base, plat.rtc_base,
            plat.gicd_base, plat.gicr_base, plat.rtc_irq, plat.timer_irq);
    if (!plat.rtc_base || !plat.gicd_base || !plat.gicr_base || !plat.rtc_irq || !plat.timer_irq)
        panic("platform devices missing");
    if (!strcmp(test, "p0-boot")) {
        kprintf("[TEST] CASE=p0-boot PASS\n");
        test_exit(0);
    }
    exception_init();
    vm_init(&plat);
    uint64_t high = ((uint64_t (*)(void))((uintptr_t)vm_high_probe + 0xffffff8000000000ULL))();
    uint64_t text = vm_query(vm_kernel_root(), (uintptr_t)kernel_main);
    uint64_t data = vm_query(vm_kernel_root(), (uintptr_t)&plat);
    extern char __image_start[], __image_end[];
    uint64_t root = vm_kernel_root(), sctlr;
    __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));
    unsigned reserved = root >= (uintptr_t)__image_start && root < (uintptr_t)__image_end &&
                        !(root >= fdt && root < fdt + plat.dtb_size);
    unsigned ro = ((text >> 6) & 3) == 2, xn = (data >> 53) & 1;
    if (!(sctlr & 1) || !ro || !xn || !reserved || high < 0xffffff8000000000ULL)
        panic("VM invariant");
    kprintf("[VM] enabled=1 granule=4096 root=0x%lx high_pc=0x%lx text_ro=%u data_xn=%u reserved=%u\n",
            root, high, ro, xn, reserved);
    vm_brk_test();
    if (strcmp(test, "p1-vm")) tasks_start(test);
    kprintf("[TEST] CASE=%s PASS\n", test);
    test_exit(0);
}
