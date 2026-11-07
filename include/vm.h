#ifndef MICRO_OS_VM_H
#define MICRO_OS_VM_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "platform.h"

#define VM_WRITE 1u
#define VM_EXEC 2u
#define VM_DEVICE 4u
#define VM_USER 8u
#define VM_PAGE_SIZE 4096u
#define VM_HIGH_BASE UINT64_C(0xffffff8000000000)

/* L2 block 或 L3 page；呼叫端不能建立 W+X 或 Device executable。 */
static inline uint64_t vm_pte_encode(uint64_t pa, unsigned flags, unsigned level)
{
    uint64_t alignment = level == 2 ? UINT64_C(0x200000) : VM_PAGE_SIZE;
    if ((level != 2 && level != 3) || pa >= (UINT64_C(1) << 40) ||
        (pa & (alignment - 1)) || (flags & ~15u) ||
        ((flags & VM_EXEC) && (flags & (VM_WRITE | VM_DEVICE))))
        return 0;
    uint64_t pte = pa | 1 | (level == 3 ? 2 : 0) | (UINT64_C(1) << 10);
    pte |= flags & VM_DEVICE ? UINT64_C(1) << 2 : UINT64_C(3) << 8;
    if (!(flags & VM_WRITE)) pte |= UINT64_C(1) << 7;
    if (flags & VM_USER) pte |= (UINT64_C(1) << 6) | (UINT64_C(1) << 11);
    if (!(flags & VM_EXEC) || (flags & VM_USER)) pte |= UINT64_C(1) << 53;
    if (!(flags & VM_EXEC) || !(flags & VM_USER)) pte |= UINT64_C(1) << 54;
    return pte;
}

void vm_init(const struct platform *platform);
uint64_t vm_kernel_root(void);
uint64_t vm_new_root(void);
int vm_map_user(uint64_t root, uint64_t va, uint64_t pa, unsigned flags);
bool vm_user_range(uint64_t root, uint64_t va, size_t length, bool write);
void vm_activate(uint64_t root);
uint64_t vm_query(uint64_t root, uint64_t va);
unsigned vm_table_pages(void);
#endif
