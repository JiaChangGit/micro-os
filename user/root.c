#include "common.h"
void user_main(void)
{
    CHECK(boot->version == UAPI_VERSION && boot->role == ROLE_ROOT);
    if (boot->test_case >= CASE_P2_MMIO && boot->test_case <= CASE_P2_FP) {
        user_syscall(SYS_YIELD);
        user_syscall(SYS_YIELD);
        report(1, (unsigned)boot->test_case);
    }
    if (boot->test_case == CASE_P3_SCHED || boot->test_case == CASE_P3_IDLE) {
        for (unsigned count = 0; count < 3; count++) sleep_ms(10);
        report(1, (unsigned)boot->test_case);
    }
    unsigned restarts = boot->test_case == CASE_P7_REGRESSION ? 100 :
                        boot->test_case == CASE_P6_PENDING ? 1 : 3;
    unsigned count = 0;
    if (boot->test_case == CASE_P6_EVENT) {
        sleep_ms(200);
        CHECK(user_call(SYS_RESTART, boot->control, 0, 0, 0, 0, 0).status == E_BUSY);
        struct syscall_result event = user_call(SYS_WAIT_DEATH, boot->control, 5000, 0, 0, 0, 0);
        CHECK(event.status == E_OK && event.word[0] == 1 && event.word[1] == 2);
        struct syscall_result result = user_call(SYS_RESTART, boot->control, 0, 0, 0, 0, 0);
        CHECK(result.status == E_OK && result.word[0] == 2);
        count = 1;
    }
    for (;;) {
        struct syscall_result event = user_call(SYS_WAIT_DEATH, boot->control, 5000, 0, 0, 0, 0);
        if (event.status == E_TIMEOUT) continue;
        CHECK(event.status == E_OK);
        CHECK((boot->test_case >= CASE_P6_RESTART && boot->test_case <= CASE_P6_EVENT) || boot->test_case == CASE_P7_REGRESSION ||
              boot->test_case == CASE_P7_DEMO);
        CHECK(count < restarts && event.word[0] == (uint64_t)count + 1);
        struct syscall_result result = user_call(SYS_RESTART, boot->control, 0, 0, 0, 0, 0);
        CHECK(result.status == E_OK && result.word[0] == (uint64_t)count + 2);
        count++;
    }
}
