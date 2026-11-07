#include "state.h"
#include <assert.h>
#include <stdio.h>
int main(void)
{
    unsigned checks = 0;
#define CHECK(c) do { assert(c); ++checks; } while (0)
    struct wait_state s = {T_RUNNING, 0};
    CHECK(!state_move(&s, T_READY, 10));
    CHECK(s.state == T_RUNNING && !s.deadline);
    CHECK(!state_move(&s, T_NTFN, 0));
    CHECK(state_move(&s, T_SLEEP, 20));
    CHECK(!state_due(&s, 19));
    CHECK(state_due(&s, 20));
    CHECK(state_move(&s, T_READY, 0));
    CHECK(!state_due(&s, UINT64_MAX));
    CHECK(state_move(&s, T_DEAD, 0));
    CHECK(!state_move(&s, T_RECV, 0));
    CHECK(state_move(&s, T_RECV, 21));
    CHECK(!state_due(&s, 20));
    CHECK(state_due(&s, 21));
    CHECK(!state_move(&s, (enum task_state)99, 0));
    printf("[HOST] state=PASS assertions=%u\n", checks);
    return 0;
}
