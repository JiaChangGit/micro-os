#include "common.h"

static void isolation_test(void)
{
    CHECK(user_syscall(999).status == E_NOSYS);
    CHECK(user_call(SYS_LOG, 0x1000, 1, 0, 0, 0, 0).status == E_FAULT);
    switch (boot->test_case) {
    case CASE_P2_MMIO:
        (void)*(volatile uint32_t *)USER_RTC_VA;
        break;
    case CASE_P2_TEXT:
        *(volatile uint32_t *)USER_IMAGE_VA = 0;
        break;
    case CASE_P2_STACK:
        *(volatile uint64_t *)(USER_STACK_TOP - (USER_STACK_PAGES + 1) * 4096) = 0;
        break;
    case CASE_P2_KERNEL:
        (void)*(volatile uint64_t *)UINT64_C(0x40080000);
        break;
    case CASE_P2_XN: {
        *(volatile uint32_t *)(USER_STACK_TOP - 4096) = 0xd65f03c0; /* ret */
        ((void (*)(void))(USER_STACK_TOP - 4096))();
        break;
    }
    case CASE_P2_FP:
        __asm__ volatile(".inst 0x1e202800" ::: "memory"); /* fadd s0, s0, s0 */
        break;
    }
    report(0, __LINE__);
}

static void ipc_test(void)
{
    CHECK(user_call(SYS_CALL, 99, 10, RTC_PING, 0, 0, 0).status == E_BAD_HANDLE);
    CHECK(user_call(SYS_RECV, boot->endpoint, 1, 0, 0, 0, 0).status == E_RIGHTS);
    CHECK(user_call(SYS_RESTART, boot->endpoint, 0, 0, 0, 0, 0).status == E_BAD_HANDLE);
    CHECK(user_call(SYS_IRQ_ACK, boot->endpoint, 0, 0, 0, 0, 0).status == E_BAD_HANDLE);
    check_ping();
    CHECK(rpc(5, RTC_SLOW, 7, 8, 9).status == E_TIMEOUT);
    CHECK(rpc(1000, RTC_PING, 0, 0, 0).status == E_BUSY);
    sleep_ms(40);
    check_ping();
    report(1, CASE_P4_IPC);
}

static void rtc_test(void)
{
    check_ping();
    struct syscall_result time = rpc(1000, RTC_GET_TIME, 0, 0, 0);
    CHECK(time.status == E_OK && time.word[0] == E_OK && time.word[1] != 0);
    struct syscall_result invalid = rpc(1000, RTC_WAIT_ALARM, 0, 0, 0);
    CHECK(invalid.status == E_OK && (int64_t)invalid.word[0] == E_INVAL);
    invalid = rpc(1000, RTC_WAIT_ALARM, 4, 0, 0);
    CHECK(invalid.status == E_OK && (int64_t)invalid.word[0] == E_INVAL);
    if (boot->test_case == CASE_P5_CLIENT_TIMEOUT) {
        CHECK(rpc(5, RTC_WAIT_ALARM, 2, 0, 0).status == E_TIMEOUT);
        CHECK(rpc(1000, RTC_PING, 0, 0, 0).status == E_BUSY);
        sleep_ms(2200);
        check_ping();
        report(1, CASE_P5_CLIENT_TIMEOUT);
    }
    struct syscall_result alarm = rpc(5000, RTC_WAIT_ALARM, 1, 0, 0);
    int64_t expected = boot->test_case == CASE_P5_TIMEOUT ? E_TIMEOUT : E_OK;
    CHECK(alarm.status == E_OK && (int64_t)alarm.word[0] == expected);
    check_ping();
    report(1, (unsigned)boot->test_case);
}

void user_main(void)
{
    CHECK(boot->version == UAPI_VERSION && boot->role == ROLE_CLIENT);
    if (boot->test_case >= CASE_P2_MMIO && boot->test_case <= CASE_P2_FP)
        isolation_test();
    if (boot->test_case == CASE_P3_SCHED || boot->test_case == CASE_P3_IDLE)
        for (;;) sleep_ms(100);
    if (boot->test_case == CASE_P4_IPC) ipc_test();
    if (boot->test_case >= CASE_P5_RTC && boot->test_case <= CASE_P5_CLIENT_TIMEOUT) rtc_test();

    if (boot->test_case == CASE_P6_PENDING) {
        check_ping();
        CHECK(rpc(5000, RTC_WAIT_ALARM, 1, 0, 0).status == E_PEER_DIED);
        check_ping();
        struct syscall_result alarm = rpc(5000, RTC_WAIT_ALARM, 1, 0, 0);
        CHECK(alarm.status == E_OK && alarm.word[0] == E_OK);
        report(1, CASE_P6_PENDING);
    }
    unsigned cycles = boot->test_case == CASE_P7_REGRESSION ? 100 : 3;
    for (unsigned cycle = 0; cycle < cycles; cycle++) {
        check_ping();
        CHECK(rpc(1000, RTC_CRASH, 0, 0, 0).status == E_PEER_DIED);
        check_ping();
    }
    if (boot->test_case == CASE_P7_DEMO) {
        struct syscall_result alarm = rpc(5000, RTC_WAIT_ALARM, 1, 0, 0);
        CHECK(alarm.status == E_OK && alarm.word[0] == E_OK);
    }
    report(1, (unsigned)boot->test_case);
}
