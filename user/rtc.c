#include "common.h"

static inline uint32_t rtc_read(unsigned offset)
{
    return *(volatile uint32_t *)(boot->rtc_va + offset);
}
static inline void rtc_write(unsigned offset, uint32_t value)
{
    *(volatile uint32_t *)(boot->rtc_va + offset) = value;
}
static inline void device_barrier(void)
{
    __asm__ volatile("dsb sy" ::: "memory");
}
static int64_t wait_alarm(uint64_t seconds)
{
    if (seconds < 1 || seconds > 3) return E_INVAL;
    rtc_write(0x10, 0);
    rtc_write(0x1c, 1);
    rtc_write(0x04, rtc_read(0) + (uint32_t)seconds);
    rtc_write(0x10, boot->test_case == CASE_P5_TIMEOUT ? 0 : 1);
    device_barrier();
    if (boot->test_case == CASE_P5_EARLY) sleep_ms(1500);
    uint64_t timeout = boot->test_case == CASE_P5_TIMEOUT ? 100 : (seconds + 2) * 1000;
    struct syscall_result event = user_call(SYS_WAIT, boot->irq, timeout, 0, 0, 0, 0);
    int64_t status = event.status;
    if (status == E_OK) {
        CHECK(event.word[0] & 1);
        CHECK(rtc_read(0x18) & 1);
        CHECK(user_call(SYS_IRQ_ACK, boot->irq, 0, 0, 0, 0, 0).status == E_BUSY);
        if (boot->test_case == CASE_P6_PENDING && boot->instance == 1)
            (void)*(volatile uint64_t *)UINT64_C(0x1000);
    }
    rtc_write(0x10, 0);
    rtc_write(0x1c, 1);
    device_barrier();
    CHECK(user_call(SYS_IRQ_ACK, boot->irq, 0, 0, 0, 0, 0).status == E_OK);
    return status;
}

void user_main(void)
{
    CHECK(boot->version == UAPI_VERSION && boot->role == ROLE_RTC);
    if (boot->test_case >= CASE_P2_MMIO && boot->test_case <= CASE_P2_FP)
        for (;;) user_syscall(SYS_YIELD);
    if (boot->test_case == CASE_P3_SCHED) {
        log_text("[USER] rtc=busy\n");
        for (;;) __asm__ volatile("nop");
    }
    if (boot->test_case == CASE_P3_IDLE)
        for (;;) sleep_ms(100);

    if (boot->instance > 1)
        CHECK(user_call(SYS_REPLY, 1, 0, 0, 0, 0, 0).status == E_STALE);
    rtc_write(0x10, 0);
    rtc_write(0x1c, 1);
    rtc_write(0x0c, 1);
    device_barrier();
    CHECK(user_call(SYS_READY, boot->endpoint, 0, 0, 0, 0, 0).status == E_OK);
    for (;;) {
        struct syscall_result request = user_call(SYS_RECV, boot->endpoint, 1000, 0, 0, 0, 0);
        if (request.status == E_TIMEOUT) continue;
        CHECK(request.status == E_OK);
        uint64_t token = request.word[0], op = request.word[1];
        uint64_t a = request.word[1], b = request.word[2];
        uint64_t c = request.word[3], d = request.word[4];
        if (op == RTC_GET_TIME) {
            a = E_OK; b = rtc_read(0); c = d = 0;
        } else if (op == RTC_WAIT_ALARM) {
            a = (uint64_t)wait_alarm(request.word[2]);
            b = rtc_read(0); c = d = 0;
        } else if (op == RTC_SLOW || op == RTC_BUSY) {
            sleep_ms(30);
        } else if (op == RTC_CRASH && boot->test_case == CASE_P6_EXIT) {
            user_syscall(SYS_EXIT);
            report(0, __LINE__);
        } else if (op == RTC_CRASH && (boot->test_case == CASE_P6_RESTART || boot->test_case == CASE_P6_EVENT ||
                   boot->test_case == CASE_P7_REGRESSION || boot->test_case == CASE_P7_DEMO)) {
            (void)*(volatile uint64_t *)UINT64_C(0x1000);
        } else if (op != RTC_PING) {
            a = (uint64_t)E_INVAL; b = c = d = 0;
        }
        int64_t status = user_call(SYS_REPLY, token, a, b, c, d, 0).status;
        if ((op == RTC_SLOW && boot->test_case == CASE_P4_IPC) ||
            (op == RTC_WAIT_ALARM && request.word[2] == 2 &&
             boot->test_case == CASE_P5_CLIENT_TIMEOUT))
            CHECK(status == E_STALE);
        else
            CHECK(status == E_OK || status == E_STALE);
    }
}
