#include "state.h"
#include <assert.h>
#include <stdio.h>
int main(void)
{
    unsigned checks = 0, client;
#define CHECK(c) do { assert(c); ++checks; } while (0)
    struct exchange x = {0};
    CHECK(exchange_open(&x, 2, 1));
    uint64_t old = x.token;
    CHECK(!exchange_open(&x, 0, 1));
    CHECK(!exchange_finish(&x, old, 2, &client));
    CHECK(!exchange_finish(&x, old + 1, 1, &client));
    CHECK(exchange_finish(&x, old, 1, &client) && client == 2);
    CHECK(!exchange_finish(&x, old, 1, &client));
    CHECK(exchange_open(&x, 2, 2));
    CHECK(!exchange_finish(&x, old, 1, &client));
    CHECK(exchange_finish(&x, x.token, 2, &client));
    /* 六種標籤順序重複呼叫同一完成函式；實際事件分支由 guest 另行測試。 */
    enum reason { REPLY, TIMEOUT, DEATH, NO_WINNER };
    const enum reason orders[][3] = {
        { REPLY, TIMEOUT, DEATH }, { REPLY, DEATH, TIMEOUT },
        { TIMEOUT, REPLY, DEATH }, { TIMEOUT, DEATH, REPLY },
        { DEATH, REPLY, TIMEOUT }, { DEATH, TIMEOUT, REPLY },
    };
    for (unsigned order = 0; order < sizeof orders / sizeof orders[0]; ++order) {
        uint64_t epoch = 3 + order;
        CHECK(exchange_open(&x, 2, epoch));
        uint64_t token = x.token;
        unsigned completions = 0;
        enum reason winner = NO_WINNER;
        for (unsigned event = 0; event < 3; ++event) {
            client = 99;
            bool completed = exchange_finish(&x, token, epoch, &client);
            CHECK(completed == (event == 0));
            CHECK(client == (completed ? 2u : 99u));
            if (completed) {
                ++completions;
                winner = orders[order][event];
            }
        }
        CHECK(completions == 1 && winner == orders[order][0]);
        CHECK(!x.active);
        /* 槽位已重用後，前一筆請求的事件也不能完成新請求。 */
        CHECK(exchange_open(&x, 2, epoch + 1));
        CHECK(!exchange_finish(&x, token, epoch, &client));
        CHECK(x.active);
        CHECK(exchange_finish(&x, x.token, epoch + 1, &client));
    }
    x.sequence = UINT64_MAX;
    CHECK(!exchange_open(&x, 2, 4));
    printf("[HOST] exchange=PASS assertions=%u\n", checks);
    return 0;
}
