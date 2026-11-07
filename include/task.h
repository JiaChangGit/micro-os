#ifndef MICRO_OS_TASK_H
#define MICRO_OS_TASK_H
#include "trap.h"
#include "state.h"
#include "test_cases.h"
void tasks_start(const char *name) __attribute__((noreturn));
struct trap *task_exception(struct trap *frame, unsigned kind);
#endif
