#include "kernel.h"
#include "vm.h"

#define TABLE_COUNT 128u
#define PA_MASK UINT64_C(0x000000fffffff000)
#define USER_LIMIT UINT64_C(0x40000000)
#define VA_MASK ((UINT64_C(1) << 39) - 1)

static uint64_t tables[TABLE_COUNT][512] __attribute__((aligned(VM_PAGE_SIZE)));
static unsigned used;
static uint64_t kernel_root;
static struct platform memory;
extern char __image_start[], __text_end[], __rodata_end[], __image_end[];

static uint64_t allocate_table(void)
{
    if (used == TABLE_COUNT) return 0;
    uint64_t *table = tables[used++];
    memset(table, 0, VM_PAGE_SIZE);
    return (uint64_t)(uintptr_t)table;
}

static uint64_t *table_address(uint64_t descriptor)
{
    return (uint64_t *)(uintptr_t)(descriptor & PA_MASK);
}

static bool owned_table(uint64_t address)
{
    uint64_t start = (uint64_t)(uintptr_t)tables;
    return !(address & (VM_PAGE_SIZE - 1)) && address >= start &&
           address - start < (uint64_t)used * VM_PAGE_SIZE;
}

static uint64_t *leaf(uint64_t root, uint64_t va, bool create)
{
    if (!owned_table(root)) return NULL;
    uint64_t *table = (uint64_t *)(uintptr_t)root;
    for (unsigned level = 1; level < 3; ++level) {
        unsigned shift = level == 1 ? 30 : 21;
        uint64_t *entry = &table[(va >> shift) & 511];
        if (!(*entry & 1)) {
            if (!create) return NULL;
            uint64_t next = allocate_table();
            if (!next) return NULL;
            *entry = next | 3;
        } else if ((*entry & 3) != 3) return entry;
        if (!owned_table(*entry & PA_MASK)) return NULL;
        table = table_address(*entry);
    }
    return &table[(va >> 12) & 511];
}

static void map_kernel_page(uint64_t va, uint64_t pa, unsigned flags)
{
    uint64_t *entry = leaf(kernel_root, va, true);
    if (!entry) panic("page table pool exhausted");
    *entry = vm_pte_encode(pa, flags, 3);
    if (!*entry) panic("invalid kernel mapping");
}

static void map_device(uint64_t base, uint64_t size)
{
    if ((base & (VM_PAGE_SIZE - 1)) || (size & (VM_PAGE_SIZE - 1)))
        panic("unaligned device range");
    for (uint64_t offset = 0; offset < size; offset += VM_PAGE_SIZE)
        map_kernel_page(base + offset, base + offset, VM_WRITE | VM_DEVICE);
}

unsigned vm_table_pages(void) { return used; }
uint64_t vm_kernel_root(void) { return kernel_root; }

uint64_t vm_query(uint64_t root, uint64_t va)
{
    uint64_t upper = va >> 39;
    if (upper != 0 && upper != (UINT64_MAX >> 39)) return 0;
    uint64_t *entry = leaf(root, va & VA_MASK, false);
    return entry ? *entry : 0;
}

uint64_t vm_new_root(void)
{
    unsigned checkpoint = used;
    uint64_t root = allocate_table();
    if (!root) return 0;
    uint64_t *dest = (uint64_t *)(uintptr_t)root;
    memcpy(dest, (void *)(uintptr_t)kernel_root, VM_PAGE_SIZE);
    /* 低 1 GiB 含 MMIO 與 user VA；L2/L3 全部分開，避免新頁污染其他 task。 */
    if (dest[0] & 1) {
        uint64_t l2 = allocate_table();
        if (!l2) goto failure;
        memcpy((void *)(uintptr_t)l2, table_address(dest[0]), VM_PAGE_SIZE);
        dest[0] = l2 | 3;
        uint64_t *entries = (uint64_t *)(uintptr_t)l2;
        for (unsigned i = 0; i < 512; ++i) {
            if ((entries[i] & 3) != 3) continue;
            uint64_t l3 = allocate_table();
            if (!l3) goto failure;
            memcpy((void *)(uintptr_t)l3, table_address(entries[i]), VM_PAGE_SIZE);
            entries[i] = l3 | 3;
        }
    }
    return root;
failure:
    used = checkpoint;
    return 0;
}

int vm_map_user(uint64_t root, uint64_t va, uint64_t pa, unsigned flags)
{
    if (root == kernel_root || !owned_table(root) || va >= USER_LIMIT ||
        (va & (VM_PAGE_SIZE - 1)) || flags & ~15u)
        return -1;
    uint64_t pte = vm_pte_encode(pa, flags | VM_USER, 3);
    if (!pte) return -1;
    if (flags & VM_DEVICE) {
        if (pa != memory.rtc_base || flags & VM_EXEC) return -1;
    } else if (pa < memory.ram_base || pa - memory.ram_base >= memory.ram_size ||
               pa < (uint64_t)(uintptr_t)__image_end)
        return -1;
    uint64_t *entry = leaf(root, va, true);
    if (!entry || *entry & 1) return -1;
    *entry = pte;
    return 0;
}

bool vm_user_range(uint64_t root, uint64_t va, size_t length, bool write)
{
    if (va >= USER_LIMIT || length > USER_LIMIT - va) return false;
    if (!length) return true;
    uint64_t last = (va + length - 1) & ~(uint64_t)(VM_PAGE_SIZE - 1);
    for (uint64_t page = va & ~(uint64_t)(VM_PAGE_SIZE - 1);; page += VM_PAGE_SIZE) {
        uint64_t pte = vm_query(root, page);
        if (!(pte & 1) || !(pte & (UINT64_C(1) << 6)) ||
            (pte & (UINT64_C(7) << 2)) ||
            (write && (pte & (UINT64_C(1) << 7))))
            return false;
        if (page == last) break;
    }
    return true;
}

void vm_activate(uint64_t root)
{
    if (!owned_table(root)) panic("invalid TTBR0 root");
    __asm__ volatile("dsb ish; msr ttbr0_el1, %0; isb; tlbi vmalle1; dsb ish; isb"
                     :: "r"(root) : "memory");
}

void vm_init(const struct platform *platform)
{
    memory = *platform;
    if (used || platform->ram_base != 0x40000000 || platform->ram_size != 0x20000000 ||
        (uint64_t)(uintptr_t)__image_end > UINT64_C(0x40200000))
        panic("unsupported RAM layout");
    kernel_root = allocate_table();
    uint64_t l2 = allocate_table();
    ((uint64_t *)(uintptr_t)kernel_root)[1] = l2 | 3;
    for (unsigned i = 0; i < 256; ++i)
        ((uint64_t *)(uintptr_t)l2)[i] = vm_pte_encode(0x40000000 + (uint64_t)i * 0x200000, VM_WRITE, 2);
    /* 將映像所在的第一個 block 拆成 4 KiB pages，保留區不另行配置。 */
    uint64_t l3 = allocate_table();
    ((uint64_t *)(uintptr_t)l2)[0] = l3 | 3;
    for (unsigned i = 0; i < 512; ++i) {
        uint64_t pa = 0x40000000 + (uint64_t)i * VM_PAGE_SIZE;
        unsigned flags = VM_WRITE;
        if (pa >= (uint64_t)(uintptr_t)__image_start && pa < (uint64_t)(uintptr_t)__text_end)
            flags = VM_EXEC;
        else if (pa >= (uint64_t)(uintptr_t)__text_end && pa < (uint64_t)(uintptr_t)__rodata_end)
            flags = 0;
        ((uint64_t *)(uintptr_t)l3)[i] = vm_pte_encode(pa, flags, 3);
    }
    map_device(platform->uart_base, 0x1000);
    map_device(platform->rtc_base, 0x1000);
    map_device(platform->gicd_base, 0x10000);
    map_device(platform->gicr_base, 0x20000);
    uint64_t mmfr, sctlr;
    __asm__ volatile("mrs %0, id_aa64mmfr0_el1; mrs %1, sctlr_el1" : "=r"(mmfr), "=r"(sctlr));
    if (((mmfr >> 28) & 15) != 0 || (sctlr & 1)) panic("unsupported MMU entry state");
    unsigned ips = (unsigned)mmfr & 15;
    if (ips > 2) ips = 2;
    uint64_t tcr = 25 | (UINT64_C(25) << 16) | (UINT64_C(1) << 8) |
        (UINT64_C(1) << 10) | (UINT64_C(3) << 12) | (UINT64_C(1) << 24) |
        (UINT64_C(1) << 26) | (UINT64_C(3) << 28) | (UINT64_C(2) << 30) |
        ((uint64_t)ips << 32);
    uint64_t mair = 0x4ff;
    __asm__ volatile("dsb sy; msr mair_el1, %0; msr tcr_el1, %1; msr ttbr0_el1, %2;"
                     "msr ttbr1_el1, %2; isb; tlbi vmalle1; dsb sy; isb"
                     :: "r"(mair), "r"(tcr), "r"(kernel_root) : "memory");
    sctlr |= 1 | (UINT64_C(1) << 2) | (UINT64_C(1) << 12) | (UINT64_C(1) << 19);
    __asm__ volatile("msr sctlr_el1, %0; isb" :: "r"(sctlr) : "memory");
}
