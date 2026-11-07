#ifndef MICRO_UAPI_H
#define MICRO_UAPI_H
#define UAPI_SYS_EXIT 2
#ifndef __ASSEMBLER__
#include <stdint.h>

#define UAPI_VERSION 1u
#define USER_BOOTINFO_VA UINT64_C(0x003ff000)
#define USER_IMAGE_VA UINT64_C(0x00400000)
#define USER_STACK_TOP UINT64_C(0x00800000)
#define USER_STACK_PAGES 4u
#define USER_RTC_VA UINT64_C(0x20000000)

enum role_id { ROLE_ROOT, ROLE_RTC, ROLE_CLIENT, ROLE_COUNT };
enum syscall_id {
    SYS_YIELD, SYS_LOG, SYS_EXIT = UAPI_SYS_EXIT, SYS_SLEEP,
    SYS_CALL, SYS_RECV, SYS_REPLY, SYS_WAIT, SYS_IRQ_ACK,
    SYS_READY, SYS_RESTART, SYS_WAIT_DEATH, SYS_REPORT, SYS_CLOCK,
};
enum syscall_error {
    E_OK = 0, E_INVAL = -1, E_BAD_HANDLE = -2, E_RIGHTS = -3,
    E_BUSY = -4, E_TIMEOUT = -5, E_PEER_DIED = -6, E_STALE = -7,
    E_FAULT = -8, E_NOT_READY = -9, E_LIMIT = -10, E_NOSYS = -11,
};
enum rtc_request { RTC_GET_TIME = 1, RTC_PING, RTC_WAIT_ALARM };
struct boot_info {
    uint64_t version, role, instance, test_case;
    uint64_t rtc_va, endpoint, irq, control;
};
_Static_assert(sizeof(struct boot_info) == 64, "BootInfo ABI");

#ifdef __aarch64__
struct syscall_result { int64_t status; uint64_t word[5]; };
static inline struct syscall_result user_call(uint64_t nr, uint64_t a0,
        uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4, uint64_t a5)
{
    register uint64_t x0 __asm__("x0") = a0;
    register uint64_t x1 __asm__("x1") = a1;
    register uint64_t x2 __asm__("x2") = a2;
    register uint64_t x3 __asm__("x3") = a3;
    register uint64_t x4 __asm__("x4") = a4;
    register uint64_t x5 __asm__("x5") = a5;
    register uint64_t x8 __asm__("x8") = nr;
    __asm__ volatile("svc #0" : "+r"(x0), "+r"(x1), "+r"(x2),
        "+r"(x3), "+r"(x4), "+r"(x5) : "r"(x8) : "memory", "cc");
    return (struct syscall_result){ (int64_t)x0, { x1, x2, x3, x4, x5 } };
}
static inline struct syscall_result user_syscall(uint64_t nr)
{
    return user_call(nr, 0, 0, 0, 0, 0, 0);
}
#endif
#endif
#endif
