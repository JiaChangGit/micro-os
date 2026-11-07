#include "vm.h"
#include <assert.h>
#include <stdio.h>

static unsigned checks;
#define CHECK(condition) do { assert(condition); ++checks; } while (0)

int main(void)
{
    uint64_t pa = 0x41000000;
    uint64_t kernel_text = vm_pte_encode(pa, VM_EXEC, 3);
    CHECK((kernel_text & 3) == 3);
    CHECK((kernel_text & (UINT64_C(3) << 6)) == (UINT64_C(2) << 6));
    CHECK(!(kernel_text & (UINT64_C(1) << 53)));
    CHECK(kernel_text & (UINT64_C(1) << 54));

    uint64_t user_text = vm_pte_encode(pa, VM_USER | VM_EXEC, 3);
    CHECK((user_text & (UINT64_C(3) << 6)) == (UINT64_C(3) << 6));
    CHECK(user_text & (UINT64_C(1) << 53));
    CHECK(!(user_text & (UINT64_C(1) << 54)));
    CHECK(user_text & (UINT64_C(1) << 11));

    uint64_t user_data = vm_pte_encode(pa, VM_USER | VM_WRITE, 3);
    CHECK((user_data & (UINT64_C(3) << 6)) == (UINT64_C(1) << 6));
    CHECK(user_data & (UINT64_C(1) << 53));
    CHECK(user_data & (UINT64_C(1) << 54));
    CHECK((user_data & (UINT64_C(3) << 8)) == (UINT64_C(3) << 8));
    CHECK(user_data & (UINT64_C(1) << 10));

    uint64_t device = vm_pte_encode(0x9010000, VM_USER | VM_WRITE | VM_DEVICE, 3);
    CHECK((device & (UINT64_C(7) << 2)) == (UINT64_C(1) << 2));
    CHECK(!(device & (UINT64_C(3) << 8)));
    uint64_t block = vm_pte_encode(0x40000000, VM_WRITE, 2);
    CHECK((block & 3) == 1);
    CHECK((block & UINT64_C(0x000000fffffff000)) == 0x40000000);

    CHECK(!vm_pte_encode(pa, VM_WRITE | VM_EXEC, 3));
    CHECK(!vm_pte_encode(pa, VM_DEVICE | VM_EXEC, 3));
    CHECK(!vm_pte_encode(pa + 1, 0, 3));
    CHECK(!vm_pte_encode(pa + 0x1000, 0, 2));
    CHECK(!vm_pte_encode(UINT64_C(1) << 40, 0, 3));
    CHECK(!vm_pte_encode(pa, 16, 3));
    CHECK(!vm_pte_encode(pa, 0, 1));
    CHECK(!vm_pte_encode(pa, 0, 4));
    printf("[HOST] pte=PASS assertions=%u\n", checks);
    return 0;
}
