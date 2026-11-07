#ifndef MICRO_USER_COMMON_H
#define MICRO_USER_COMMON_H
#include "uapi.h"
#include "test_cases.h"

static const struct boot_info *const boot = (const struct boot_info *)USER_BOOTINFO_VA;
static inline void report(int success, unsigned code)
{
    user_call(SYS_REPORT, (uint64_t)success, code, 0, 0, 0, 0);
    for (;;) user_call(SYS_SLEEP, 1000, 0, 0, 0, 0, 0);
}
#define CHECK(condition) do { if (!(condition)) report(0, __LINE__); } while (0)
static inline void sleep_ms(uint64_t ms)
{
    CHECK(user_call(SYS_SLEEP, ms, 0, 0, 0, 0, 0).status == E_OK);
}
static inline void log_text(const char *message)
{
    uint64_t length = 0;
    while (message[length]) length++;
    CHECK(user_call(SYS_LOG, (uint64_t)message, length, 0, 0, 0, 0).status == E_OK);
}
static inline struct syscall_result rpc(uint64_t timeout, uint64_t op,
        uint64_t a, uint64_t b, uint64_t c)
{
    return user_call(SYS_CALL, boot->endpoint, timeout, op, a, b, c);
}
/* 只對冪等 PING 重試；故障與 alarm 請求不在這裡重送。 */
static inline struct syscall_result ping_ready(void)
{
    struct syscall_result result = { .status = E_NOT_READY };
    for (unsigned attempt = 0; attempt < 1000; attempt++) {
        result = rpc(1000, RTC_PING, 0x11, 0x22, 0x33);
        if (result.status != E_NOT_READY && result.status != E_BUSY && result.status != E_PEER_DIED)
            return result;
        sleep_ms(1);
    }
    return result;
}
static inline void check_ping(void)
{
    struct syscall_result result = ping_ready();
    CHECK(result.status == E_OK);
    CHECK(result.word[0] == RTC_PING && result.word[1] == 0x11 &&
          result.word[2] == 0x22 && result.word[3] == 0x33);
}
#endif
