#include "kernel.h"
#include "task.h"
#include "uapi.h"
#include "vm.h"
#include "irq.h"
#include "user_images.h"

#define KSTACK_SIZE 8192u
#define ROLE_BYTES UINT64_C(0x20000)
#define FRAME_BASE UINT64_C(0x41000000)
#define TASKS 4u
#define IDLE 3u
#define IRQ_BIT 1u
#define MAX_MS 10000u

struct task {
    struct wait_state wait;
    struct trap *frame;
    uint64_t root, instance, pending;
    unsigned priority;
    bool entered;
};
static struct task task[TASKS];
static unsigned char stacks[TASKS][KSTACK_SIZE] __attribute__((aligned(4096)));
static unsigned current = IDLE, test_id, baseline_tables;
static const char *test_name;
static uint64_t frequency, epoch = 1, irq_instance;
static bool endpoint_ready, rtc_masked = true, death_pending;
static struct exchange rpc;
static struct {
    unsigned svc, invalid, bad_pointer, continued, fault_ec;
    uint64_t fault_far;
    unsigned timer_irq, preemptions, sleep_wake, idle;
    unsigned calls, replies, busy, timeout, stale, denied, completed;
    unsigned alarm, irq, device_timeout, early, ack, ack_rejected;
    unsigned restarts, peer_died, old_token, masked;
} stats;

extern void enter_frame(struct trap *) __attribute__((noreturn));

static bool isolation(void) { return test_id >= CASE_P2_MMIO && test_id <= CASE_P2_FP; }
static uint64_t frame_base(unsigned id) { return FRAME_BASE + ROLE_BYTES * id; }
static void move(unsigned id, enum task_state state, uint64_t deadline)
{
    if (!state_move(&task[id].wait, state, deadline)) panic("invalid state transition");
}
static uint64_t deadline_ms(uint64_t ms)
{
    if (!ms || ms > MAX_MS) return 0;
    uint64_t now = timer_now(), delta = (frequency * ms + 999) / 1000;
    return now <= UINT64_MAX - delta ? now + delta : 0;
}
static void result(unsigned id, int64_t value)
{
    task[id].frame->x[0] = (uint64_t)value;
}
static void wake(unsigned id, int64_t value)
{
    result(id, value);
    move(id, T_READY, 0);
}

static void sync_code(uint64_t base, uint64_t size)
{
    uint64_t ctr;
    __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
    uint64_t line = UINT64_C(4) << ((ctr >> 16) & 15);
    for (uint64_t p = base & ~(line - 1); p < base + size; p += line)
        __asm__ volatile("dc cvau, %0" :: "r"(p) : "memory");
    __asm__ volatile("dsb ish; ic iallu; dsb ish; isb" ::: "memory");
}

static void load_role(unsigned id, bool first)
{
    struct task *t = &task[id];
    const struct user_image *image = &user_images[id];
    uint64_t base = frame_base(id), cursor = base + VM_PAGE_SIZE;
    if (first) {
        t->root = vm_new_root();
        if (!t->root) panic("task root exhausted");
    }
    memset((void *)(uintptr_t)base, 0, ROLE_BYTES);
    for (size_t n = 0; n < image->segment_count; ++n) {
        const struct user_segment *s = &image->segments[n];
        uint64_t size = (s->mem_size + VM_PAGE_SIZE - 1) & ~(uint64_t)(VM_PAGE_SIZE - 1);
        if (size > ROLE_BYTES || cursor > base + ROLE_BYTES - 0x4000 - size)
            panic("fixed image exceeds frame reservation");
        memcpy((void *)(uintptr_t)cursor, s->data, s->file_size);
        unsigned flags = (s->flags & USER_W ? VM_WRITE : 0) | (s->flags & USER_X ? VM_EXEC : 0);
        if (first) for (uint64_t p = 0; p < size; p += VM_PAGE_SIZE)
            if (vm_map_user(t->root, s->va + p, cursor + p, flags)) panic("user segment mapping");
        cursor += size;
    }
    if (first) {
        if (vm_map_user(t->root, USER_BOOTINFO_VA, base, 0)) panic("bootinfo mapping");
        for (unsigned p = 0; p < USER_STACK_PAGES; ++p)
            if (vm_map_user(t->root, USER_STACK_TOP - 0x4000 + p * VM_PAGE_SIZE,
                            base + ROLE_BYTES - 0x4000 + p * VM_PAGE_SIZE, VM_WRITE))
                panic("stack mapping");
        if (id == ROLE_RTC && vm_map_user(t->root, USER_RTC_VA, plat.rtc_base, VM_DEVICE | VM_WRITE))
            panic("RTC mapping");
        if (vm_query(t->root, USER_STACK_TOP - 0x5000) & 1) panic("stack guard mapped");
    }
    struct boot_info *bi = (void *)(uintptr_t)base;
    *bi = (struct boot_info){UAPI_VERSION, id, t->instance, test_id,
        id == ROLE_RTC ? USER_RTC_VA : 0, 1, id == ROLE_RTC ? 2 : 0, id == ROLE_ROOT ? 3 : 0};
    memset(stacks[id], 0, KSTACK_SIZE);
    t->frame = (struct trap *)(void *)(stacks[id] + KSTACK_SIZE - sizeof(struct trap));
    t->frame->elr = image->entry;
    t->frame->sp = USER_STACK_TOP;
    t->frame->spsr = isolation() ? 0x3c0 : 0x340; /* EL0t；P2 仍遮蔽 IRQ。 */
    t->frame->x[0] = USER_BOOTINFO_VA;
    t->pending = 0;
    t->entered = false;
    move(id, T_READY, 0);
    sync_code(base, ROLE_BYTES);
}

static void invariant(void)
{
    unsigned running = 0;
    for (unsigned n = 0; n < TASKS; ++n) {
        struct task *t = &task[n];
        running += t->wait.state == T_RUNNING;
        if (t->wait.state > T_DEAD) panic("state range");
        bool timed = t->wait.state == T_SLEEP || t->wait.state == T_RECV || t->wait.state == T_REPLY ||
                     t->wait.state == T_NTFN || t->wait.state == T_DEATH;
        if (timed != (t->wait.deadline != 0)) panic("wait deadline invariant");
    }
    if (running > 1) panic("multiple running tasks");
    if (rpc.active && task[rpc.client].wait.state != T_REPLY) panic("exchange wait invariant");
    if (task[ROLE_RTC].wait.state == T_DEAD && (!rtc_masked || endpoint_ready))
        panic("dead RTC exposed");
    if (stats.completed != stats.replies + stats.timeout + stats.peer_died)
        panic("exchange completion accounting");
}

static bool complete(uint64_t token, int64_t status, const uint64_t *words, unsigned reason)
{
    unsigned client;
    if (!exchange_finish(&rpc, token, epoch, &client)) return false;
    if (task[client].wait.state != T_REPLY) panic("double completion");
    for (unsigned n = 0; n < 4; ++n) task[client].frame->x[n + 1] = words ? words[n] : 0;
    wake(client, status);
    ++stats.completed;
    if (reason == 0) ++stats.replies;
    else if (reason == 1) ++stats.timeout;
    else ++stats.peer_died;
    return true;
}

static void expire(uint64_t now)
{
    for (unsigned n = 0; n < ROLE_COUNT; ++n) {
        if (!state_due(&task[n].wait, now)) continue;
        switch (task[n].wait.state) {
        case T_SLEEP: ++stats.sleep_wake; wake(n, E_OK); break;
        case T_REPLY:
            if (!complete(rpc.token, E_TIMEOUT, NULL, 1)) panic("timeout exchange missing");
            break;
        case T_NTFN: ++stats.device_timeout; wake(n, E_TIMEOUT); break;
        case T_RECV: wake(n, E_TIMEOUT); break;
        case T_DEATH: wake(n, E_TIMEOUT); break;
        default: panic("deadline without wait");
        }
    }
}

static void arm_timer(void)
{
    uint64_t next = timer_now() + frequency / 200; /* 最長 5 ms time slice。 */
    for (unsigned n = 0; n < ROLE_COUNT; ++n)
        if (task[n].wait.deadline && task[n].wait.deadline < next) next = task[n].wait.deadline;
    timer_set(next);
}

static struct trap *schedule(bool rotate)
{
    unsigned previous = current, chosen = IDLE;
    if (task[current].wait.state == T_RUNNING) move(current, T_READY, 0);
    if (isolation() && !rotate && task[previous].wait.state == T_READY) chosen = previous;
    else if (isolation()) {
        for (unsigned step = 1; step <= ROLE_COUNT; ++step) {
            unsigned id = (previous + step) % ROLE_COUNT;
            if (task[id].wait.state == T_READY) { chosen = id; break; }
        }
    } else {
        (void)rotate;
        for (unsigned n = 0; n < ROLE_COUNT; ++n)
            if (task[n].wait.state == T_READY &&
                (chosen == IDLE || task[n].priority < task[chosen].priority)) chosen = n;
    }
    current = chosen;
    move(chosen, T_RUNNING, 0);
    invariant();
    if (chosen == IDLE) ++stats.idle;
    vm_activate(chosen == IDLE ? vm_kernel_root() : task[chosen].root);
    if (!isolation()) arm_timer();
    return task[chosen].frame;
}

static _Noreturn void idle_loop(void)
{
    for (;;) __asm__ volatile("wfi" ::: "memory");
}

static void rtc_fault(struct trap *frame)
{
    if (!isolation()) irq_disable(plat.rtc_irq);
    rtc_masked = true;
    ++stats.masked;
    /* 原 client 必須在 epoch 更新前以原 exchange identity 完成。 */
    if (rpc.active && !complete(rpc.token, E_PEER_DIED, NULL, 2)) panic("peer completion");
    endpoint_ready = false;
    irq_instance = 0;
    if (epoch == UINT64_MAX) panic("endpoint epoch exhausted");
    ++epoch;
    task[ROLE_RTC].pending = 0;
    move(ROLE_RTC, T_DEAD, 0);
    death_pending = true;
    kprintf("[DEATH] role=1 instance=%lu epoch=%lu esr=0x%lx far=0x%lx masked=1 device_pending=%u\n",
            task[1].instance, epoch, frame->esr, frame->far, mmio_read(plat.rtc_base + 0x14) & 1);
    if (task[ROLE_ROOT].wait.state == T_DEATH) {
        task[0].frame->x[1] = task[1].instance;
        task[0].frame->x[2] = epoch;
        wake(ROLE_ROOT, E_OK);
        death_pending = false;
    }
}

enum object_type { OBJ_ENDPOINT, OBJ_IRQ, OBJ_CONTROL };
enum rights { R_CALL = 1, R_RECV = 2, R_READY = 4, R_WAIT = 8, R_ACK = 16, R_RESTART = 32, R_DEATH = 64 };
static int capability(uint64_t handle, enum object_type type, unsigned right)
{
    enum object_type actual;
    unsigned available;
    if (handle == 1) { actual = OBJ_ENDPOINT; available = current == ROLE_RTC ? R_RECV | R_READY : R_CALL; }
    else if (handle == 2 && current == ROLE_RTC) { actual = OBJ_IRQ; available = R_WAIT | R_ACK; }
    else if (handle == 3 && current == ROLE_ROOT) { actual = OBJ_CONTROL; available = R_RESTART | R_DEATH; }
    else { ++stats.denied; return E_BAD_HANDLE; }
    if (actual != type) { ++stats.denied; return E_BAD_HANDLE; }
    if (!(available & right)) { ++stats.denied; return E_RIGHTS; }
    return E_OK;
}

static _Noreturn void report(uint64_t success, uint64_t code)
{
    if (success != 1) {
        kprintf("[USER_FAIL] role=%u code=%lu\n", current, code);
        panic("user assertion");
    }
    invariant();
    if (isolation()) {
        if (current != ROLE_ROOT || task[2].wait.state != T_DEAD || !stats.invalid || !stats.bad_pointer)
            panic("isolation observations incomplete");
        ++stats.continued;
        kprintf("[ISOLATION] role=2 ec=%u far=0x%lx svc=%u invalid_syscall=%u bad_pointer=%u continued=%u\n",
            stats.fault_ec, stats.fault_far, stats.svc, stats.invalid, stats.bad_pointer, stats.continued);
    } else {
        kprintf("[SCHED] timer_irq=%u preemptions=%u sleep_wake=%u idle=%u invariants=1\n",
            stats.timer_irq, stats.preemptions, stats.sleep_wake, stats.idle);
        kprintf("[IPC] calls=%u replies=%u busy=%u timeout=%u stale=%u denied=%u completed=%u once=1\n",
            stats.calls, stats.replies, stats.busy, stats.timeout, stats.stale, stats.denied, stats.completed);
        kprintf("[RTC] alarm=%u irq=%u timeout=%u early=%u ack=%u ack_rejected=%u\n",
            stats.alarm, stats.irq, stats.device_timeout, stats.early, stats.ack, stats.ack_rejected);
        kprintf("[RESTART] count=%u peer_died=%u old_token=%u masked=%u resource_delta=%u\n",
            stats.restarts, stats.peer_died, stats.old_token, stats.masked, vm_table_pages() - baseline_tables);
    }
    kprintf("[TEST] CASE=%s PASS\n", test_name);
    test_exit(0);
}

static void syscall(struct trap *f)
{
    uint64_t nr = f->x[8], a[6];
    memcpy(a, f->x, sizeof(a));
    result(current, E_OK);
    ++stats.svc;
    if (!task[current].entered) {
        task[current].entered = true;
        kprintf("[TASK] role=%u el=0 instance=%lu\n", current, task[current].instance);
    }
    int error;
    uint64_t deadline;
    switch (nr) {
    case SYS_YIELD: break;
    case SYS_LOG:
        if (a[1] > 256 || !vm_user_range(task[current].root, a[0], a[1], false)) {
            ++stats.bad_pointer; result(current, E_FAULT); break;
        }
        for (uint64_t n = 0; n < a[1]; ++n) kprintf("%s", (char[2]){((char *)(uintptr_t)a[0])[n], 0});
        break;
    case SYS_EXIT:
        if (current == ROLE_RTC) rtc_fault(f);
        else panic("fixed role exited");
        break;
    case SYS_SLEEP:
        deadline = deadline_ms(a[0]);
        if (!deadline) result(current, E_INVAL);
        else move(current, T_SLEEP, deadline);
        break;
    case SYS_CLOCK: f->x[1] = timer_now() / (frequency / 1000); break;
    case SYS_CALL:
        error = capability(a[0], OBJ_ENDPOINT, R_CALL);
        deadline = deadline_ms(a[1]);
        if (error) result(current, error);
        else if (!deadline) result(current, E_INVAL);
        else if (task[1].wait.state == T_DEAD) result(current, E_PEER_DIED);
        else if (!endpoint_ready) result(current, E_NOT_READY);
        else if (task[1].wait.state != T_RECV || rpc.active) { ++stats.busy; result(current, E_BUSY); }
        else {
            if (!exchange_open(&rpc, current, epoch)) { result(current, E_LIMIT); break; }
            ++stats.calls;
            if (a[2] == RTC_WAIT_ALARM) ++stats.alarm;
            task[1].frame->x[1] = rpc.token;
            for (unsigned n = 0; n < 4; ++n) task[1].frame->x[n + 2] = a[n + 2];
            wake(1, E_OK);
            move(current, T_REPLY, deadline);
        }
        break;
    case SYS_RECV:
        error = capability(a[0], OBJ_ENDPOINT, R_RECV);
        deadline = deadline_ms(a[1]);
        if (error) result(current, error);
        else if (!deadline) result(current, E_INVAL);
        else if (!endpoint_ready) result(current, E_NOT_READY);
        else move(current, T_RECV, deadline);
        break;
    case SYS_REPLY:
        if (current != ROLE_RTC) { ++stats.denied; result(current, E_RIGHTS); }
        else if (!complete(a[0], E_OK, a + 1, 0)) {
            ++stats.stale;
            if (task[1].instance > 1) ++stats.old_token;
            result(current, E_STALE);
        }
        break;
    case SYS_WAIT:
        error = capability(a[0], OBJ_IRQ, R_WAIT);
        deadline = deadline_ms(a[1]);
        if (error) result(current, error);
        else if (!deadline) result(current, E_INVAL);
        else if (!endpoint_ready || irq_instance != task[current].instance) result(current, E_NOT_READY);
        else if (task[current].pending) {
            ++stats.early;
            f->x[1] = task[current].pending; task[current].pending = 0;
        } else move(current, T_NTFN, deadline);
        break;
    case SYS_IRQ_ACK:
        error = capability(a[0], OBJ_IRQ, R_ACK);
        if (error) result(current, error);
        else if (!endpoint_ready || irq_instance != task[current].instance) result(current, E_NOT_READY);
        else if (mmio_read(plat.rtc_base + 0x18) & 1) { ++stats.ack_rejected; result(current, E_BUSY); }
        else {
            task[current].pending = 0;
            irq_enable(plat.rtc_irq); rtc_masked = false; ++stats.ack;
        }
        break;
    case SYS_READY:
        error = capability(a[0], OBJ_ENDPOINT, R_READY);
        if (error) result(current, error);
        else if (endpoint_ready) result(current, E_BUSY);
        else if (mmio_read(plat.rtc_base + 0x10) || mmio_read(plat.rtc_base + 0x14))
            result(current, E_NOT_READY);
        else {
            irq_instance = task[current].instance;
            endpoint_ready = true;
            irq_enable(plat.rtc_irq); rtc_masked = false;
        }
        break;
    case SYS_WAIT_DEATH:
        error = capability(a[0], OBJ_CONTROL, R_DEATH);
        deadline = deadline_ms(a[1]);
        if (error) result(current, error);
        else if (!deadline) result(current, E_INVAL);
        else if (death_pending) { death_pending = false; f->x[1] = task[1].instance; f->x[2] = epoch; }
        else move(current, T_DEATH, deadline);
        break;
    case SYS_RESTART:
        error = capability(a[0], OBJ_CONTROL, R_RESTART);
        if (error) result(current, error);
        else if (task[1].wait.state != T_DEAD) result(current, E_BUSY);
        else if (death_pending) {
            kprintf("[RESTART_WAIT] instance=%lu epoch=%lu\n", task[1].instance, epoch);
            result(current, E_BUSY);
        }
        else if (stats.restarts == 128 || task[1].instance == UINT64_MAX) result(current, E_LIMIT);
        else {
            if (!rtc_masked || rpc.active) panic("restart before cleanup");
            ++task[1].instance;
            ++stats.restarts;
            /* 此時在 root stack；RTC 的舊 stack 已不在使用中。 */
            load_role(ROLE_RTC, false);
            f->x[1] = task[1].instance;
            kprintf("[REBUILD] instance=%lu epoch=%lu tables=%u\n", task[1].instance, epoch, vm_table_pages());
        }
        break;
    case SYS_REPORT: report(a[0], a[1]);
    default: ++stats.invalid; result(current, E_NOSYS); break;
    }
}

struct trap *task_exception(struct trap *frame, unsigned kind)
{
    struct task *t = &task[current];
    uintptr_t address = (uintptr_t)frame;
    if (address < (uintptr_t)stacks[current] || address + sizeof(*frame) > (uintptr_t)stacks[current] + KSTACK_SIZE)
        panic("trap stack range");
    t->frame = frame;
    if (!isolation()) expire(timer_now()); /* deadline 相等時 timeout 優先。 */
    if (kind == 1) {
        uint32_t id = irq_acknowledge();
        if (id == plat.timer_irq) {
            timer_stop();
            ++stats.timer_irq;
            if (current != ROLE_ROOT && task[0].wait.state == T_READY && current != IDLE)
                ++stats.preemptions;
            irq_eoi(id);
        } else if (id == plat.rtc_irq) {
            irq_disable(id); rtc_masked = true; ++stats.irq;
            irq_eoi(id);
            if (endpoint_ready && irq_instance == task[1].instance && task[1].wait.state != T_DEAD) {
                task[1].pending |= IRQ_BIT;
                if (task[1].wait.state == T_NTFN) {
                    task[1].frame->x[1] = task[1].pending; task[1].pending = 0;
                    wake(1, E_OK);
                }
            }
        } else if (id < 1020) { irq_eoi(id); panic("unbound IRQ"); }
    } else if (kind == 0 && (frame->spsr & 15) == 0 && (frame->esr >> 26) == 21) {
        syscall(frame);
    } else if (kind == 0 && (frame->spsr & 15) == 0) {
        kprintf("[FAULT] role=%u instance=%lu esr=0x%lx far=0x%lx elr=0x%lx\n",
                current, t->instance, frame->esr, frame->far, frame->elr);
        if (isolation() && current == ROLE_CLIENT) {
            stats.fault_ec = (unsigned)(frame->esr >> 26);
            stats.fault_far = stats.fault_ec == 7 ? 0 : frame->far;
            move(current, T_DEAD, 0);
        } else if (current == ROLE_RTC) rtc_fault(frame);
        else panic("unrecoverable user role");
    } else {
        kprintf("[KERNEL_FAULT] kind=%u esr=0x%lx far=0x%lx elr=0x%lx spsr=0x%lx\n",
                kind, frame->esr, frame->far, frame->elr, frame->spsr);
        panic("unexpected EL1 exception");
    }
    return schedule(kind != 0 || (frame->esr >> 26) != 21 || frame->x[8] == SYS_YIELD);
}

void tasks_start(const char *name)
{
    static const struct { const char *name; unsigned id; } cases[] = {
        {"p2-mmio", CASE_P2_MMIO}, {"p2-text", CASE_P2_TEXT}, {"p2-stack", CASE_P2_STACK},
        {"p2-kernel", CASE_P2_KERNEL}, {"p2-xn", CASE_P2_XN}, {"p2-fp", CASE_P2_FP},
        {"p3-sched", CASE_P3_SCHED}, {"p3-idle", CASE_P3_IDLE}, {"p4-ipc", CASE_P4_IPC},
        {"p5-rtc", CASE_P5_RTC}, {"p5-timeout", CASE_P5_TIMEOUT}, {"p5-early", CASE_P5_EARLY},
        {"p5-client-timeout", CASE_P5_CLIENT_TIMEOUT},
        {"p6-restart", CASE_P6_RESTART}, {"p6-pending", CASE_P6_PENDING}, {"p6-exit", CASE_P6_EXIT},
        {"p6-event", CASE_P6_EVENT},
        {"p7-regression", CASE_P7_REGRESSION}, {"p7-demo", CASE_P7_DEMO},
    };
    for (size_t n = 0; n < sizeof(cases) / sizeof(cases[0]); ++n)
        if (!strcmp(name, cases[n].name)) test_id = cases[n].id;
    if (!test_id) panic("unknown test case");
    test_name = name;
    extern char __image_end[];
    uint64_t end = FRAME_BASE + ROLE_COUNT * ROLE_BYTES;
    if (FRAME_BASE < (uintptr_t)__image_end || end > plat.ram_base + plat.ram_size ||
        (FRAME_BASE < boot_fdt + plat.dtb_size && boot_fdt < end))
        panic("role frame reservation overlaps");
    __asm__ volatile("msr cpacr_el1, xzr; isb" ::: "memory");
    frequency = timer_frequency();
    if (frequency < 1000 || frequency > UINT64_C(10000000000)) panic("counter frequency range");
    if (!isolation()) irq_init(&plat);
    for (unsigned n = 0; n < ROLE_COUNT; ++n) {
        task[n].instance = 1;
        task[n].priority = n;
        load_role(n, true);
    }
    task[IDLE].frame = (struct trap *)(void *)(stacks[IDLE] + KSTACK_SIZE - sizeof(struct trap));
    task[IDLE].frame->elr = (uintptr_t)idle_loop;
    task[IDLE].frame->spsr = 0x345;
    move(IDLE, T_READY, 0);
    baseline_tables = vm_table_pages();
    /* 從 boot stack 進入第一個 task；之後每個 trap 都在該 task 的 stack。 */
    current = ROLE_CLIENT;
    enter_frame(schedule(true));
}
