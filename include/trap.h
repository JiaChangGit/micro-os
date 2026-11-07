#ifndef MICRO_TRAP_H
#define MICRO_TRAP_H
#define TF_SIZE 288
#define TF_SP 248
#define TF_ELR 256
#define TF_SPSR 264
#define TF_ESR 272
#define TF_FAR 280
#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stddef.h>
struct trap {
    uint64_t x[31], sp, elr, spsr, esr, far;
};
_Static_assert(sizeof(struct trap) == TF_SIZE, "trap size");
_Static_assert(offsetof(struct trap, sp) == TF_SP, "trap SP");
_Static_assert(offsetof(struct trap, elr) == TF_ELR, "trap ELR");
_Static_assert(offsetof(struct trap, far) == TF_FAR, "trap FAR");
struct trap *exception_dispatch(struct trap *frame, unsigned irq);
void exception_init(void);
void vm_brk_test(void);
uint64_t vm_high_probe(void);
extern char vectors[];
#endif
#endif
