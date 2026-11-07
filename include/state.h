#ifndef MICRO_OS_STATE_H
#define MICRO_OS_STATE_H
#include <stdbool.h>
#include <stdint.h>

enum task_state { T_READY, T_RUNNING, T_SLEEP, T_RECV, T_REPLY, T_NTFN, T_DEATH, T_DEAD };
struct wait_state { enum task_state state; uint64_t deadline; };
/* 只有有限等待可帶 deadline；0 表示沒有 deadline。 */
bool state_move(struct wait_state *s, enum task_state next, uint64_t deadline);
bool state_due(const struct wait_state *s, uint64_t now);

struct exchange { uint64_t sequence, token, epoch; unsigned client; bool active; };
bool exchange_open(struct exchange *x, unsigned client, uint64_t epoch);
bool exchange_finish(struct exchange *x, uint64_t token, uint64_t epoch, unsigned *client);
#endif
