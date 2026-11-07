#include "kernel.h"
#include "trap.h"
#include "task.h"
void exception_init(void)
{
    __asm__ volatile("msr vbar_el1, %0; isb" :: "r"(vectors) : "memory");
}
struct trap *exception_dispatch(struct trap *frame, unsigned irq)
{
    unsigned ec = (unsigned)(frame->esr >> 26);
    if (!irq && ec == 60 && frame->elr == (uintptr_t)vm_brk_test) {
        frame->elr += 4;
        kprintf("[EXCEPTION] el=1 ec=%u recovered=1\n", ec);
        return frame;
    }
    return task_exception(frame, irq);
}
