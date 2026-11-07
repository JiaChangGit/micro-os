#include "state.h"
#include <stddef.h>

bool state_move(struct wait_state *s, enum task_state next, uint64_t deadline)
{
    if (!s || next > T_DEAD || (deadline && next != T_SLEEP && next != T_RECV && next != T_REPLY &&
                              next != T_NTFN && next != T_DEATH)) return false;
    if ((next == T_SLEEP || next == T_RECV || next == T_REPLY || next == T_NTFN || next == T_DEATH) && !deadline)
        return false;
    s->state = next;
    s->deadline = deadline;
    return true;
}
bool state_due(const struct wait_state *s, uint64_t now)
{
    return s && s->deadline && now >= s->deadline;
}
bool exchange_open(struct exchange *x, unsigned client, uint64_t epoch)
{
    if (!x || x->active || x->sequence == UINT64_MAX || !epoch) return false;
    x->token = ++x->sequence;
    x->epoch = epoch;
    x->client = client;
    x->active = true;
    return true;
}
bool exchange_finish(struct exchange *x, uint64_t token, uint64_t epoch, unsigned *client)
{
    if (!x || !x->active || x->token != token || x->epoch != epoch) return false;
    if (client) *client = x->client;
    x->active = false;
    return true;
}
