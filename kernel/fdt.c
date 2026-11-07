#include "platform.h"
#include <stdbool.h>

#define MAX_DEPTH 16
#define MEMORY 1u
#define UART 2u
#define RTC 4u
#define GIC 8u
#define TIMER 16u
#define CHOSEN 32u

struct node {
    const uint8_t *reg, *irq, *ranges, *bootargs;
    uint32_t reg_len, irq_len, ranges_len, bootargs_len;
    uint32_t address_cells, size_cells, parent_address_cells, parent_size_cells;
    uint32_t kind, irq_parent, phandle, interrupt_cells, properties;
    bool unsafe_address, children, enabled, chosen, interrupt_controller;
};

static uint32_t be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

static uint64_t be64(const uint8_t *p)
{
    return ((uint64_t)be32(p) << 32) | be32(p + 4);
}

static bool equal(const uint8_t *p, size_t len, const char *text)
{
    size_t i = 0;
    while (i < len && text[i] && p[i] == (uint8_t)text[i])
        ++i;
    return i < len && p[i] == 0 && text[i] == 0;
}

static bool span(uint32_t offset, uint32_t length, uint32_t total)
{
    return offset >= 40 && offset <= total && length <= total - offset;
}

static bool overlap(uint32_t a, uint32_t an, uint32_t b, uint32_t bn)
{
    return a < b + bn && b < a + an;
}

/* 固定映像與 task frame 必須避開 DTB reserve map；end 為開區間。 */
static bool reserved_conflicts(uint64_t address, uint64_t size)
{
    uint64_t end = address + size;
    return (address < UINT64_C(0x40200000) && end > UINT64_C(0x40080000)) ||
           (address < UINT64_C(0x41060000) && end > UINT64_C(0x41000000));
}

static bool cells(const uint8_t *p, uint32_t count, uint64_t *value)
{
    if (count < 1 || count > 2)
        return false;
    *value = count == 2 ? be64(p) : be32(p);
    return true;
}

static bool direct_bus(const struct node *n, bool root)
{
    /* 本版不做地址翻譯；nonempty ranges 的 window 不推定為 identity。 */
    return root || (n->ranges && n->ranges_len == 0);
}

static bool reg_pair(const struct node *n, uint32_t index, uint32_t count,
                     uint64_t *base, uint64_t *size)
{
    uint32_t ac = n->parent_address_cells, sc = n->parent_size_cells;
    uint32_t step = (ac + sc) * 4;
    if (!n->reg || !ac || ac > 2 || !sc || sc > 2 || n->reg_len != count * step ||
        !cells(n->reg + index * step, ac, base) ||
        !cells(n->reg + index * step + ac * 4, sc, size))
        return false;
    return *size && *base <= UINT64_MAX - *size;
}

static bool interrupt(const struct node *n, uint32_t index, uint32_t type,
                      uint32_t *irq)
{
    if (!n->irq || n->irq_len % 12 || n->irq_len < (index + 1) * 12)
        return false;
    const uint8_t *p = n->irq + index * 12;
    uint32_t number = be32(p + 4);
    if (be32(p) != type || (be32(p + 8) & 15) != 4 ||
        number >= (type ? 16u : 988u))
        return false;
    *irq = number + (type ? 16u : 32u);
    return true;
}

static bool finish(const struct node *n, struct platform *p, uint32_t *seen,
                   uint32_t *gic_parent, uint32_t *rtc_parent, uint32_t *timer_parent)
{
    if (n->chosen && n->enabled) {
        if (*seen & CHOSEN)
            return false;
        *seen |= CHOSEN;
        for (uint32_t i = 0; i < n->bootargs_len; ++i)
            p->bootargs[i] = (char)n->bootargs[i];
    }
    if (!n->kind || !n->enabled)
        return true;
    if ((*seen & n->kind) || (n->kind & (n->kind - 1)) || n->unsafe_address)
        return false;
    uint64_t base, size;
    if (n->kind != TIMER && !reg_pair(n, 0, n->kind == GIC ? 2 : 1, &base, &size))
        return false;
    switch (n->kind) {
    case MEMORY:
        p->ram_base = base;
        p->ram_size = size;
        break;
    case UART:
        if (size < 0x1000)
            return false;
        p->uart_base = base;
        break;
    case RTC:
        if (size < 0x1000 || !interrupt(n, 0, 0, &p->rtc_irq))
            return false;
        p->rtc_base = base;
        *rtc_parent = n->irq_parent;
        break;
    case GIC:
        p->gicd_base = base;
        if (!n->phandle || n->interrupt_cells != 3 || !n->interrupt_controller ||
            size < 0x10000 || !reg_pair(n, 1, 2, &p->gicr_base, &size) || size < 0x20000)
            return false;
        *gic_parent = n->phandle;
        break;
    case TIMER:
        if (!interrupt(n, 1, 1, &p->timer_irq))
            return false;
        *timer_parent = n->irq_parent;
        break;
    default:
        return false;
    }
    *seen |= n->kind;
    return true;
}

static bool property(struct node *n, const uint8_t *name, const uint8_t *value, uint32_t len)
{
    uint32_t flag = 0;
    if (equal(name, 32, "#address-cells")) {
        flag = 1;
        if (len != 4 || !be32(value) || be32(value) > 4)
            return false;
        n->address_cells = be32(value);
    } else if (equal(name, 32, "#size-cells")) {
        flag = 2;
        if (len != 4 || be32(value) > 4)
            return false;
        n->size_cells = be32(value);
    } else if (equal(name, 32, "reg")) {
        flag = 4;
        n->reg = value;
        n->reg_len = len;
    } else if (equal(name, 32, "interrupts")) {
        flag = 8;
        n->irq = value;
        n->irq_len = len;
    } else if (equal(name, 32, "ranges")) {
        flag = 16;
        n->ranges = value;
        n->ranges_len = len;
    } else if (equal(name, 32, "interrupt-parent") || equal(name, 32, "phandle")) {
        bool parent = equal(name, 32, "interrupt-parent");
        flag = parent ? 32 : 64;
        if (len != 4 || !be32(value))
            return false;
        if (parent)
            n->irq_parent = be32(value);
        else
            n->phandle = be32(value);
    } else if (equal(name, 32, "compatible")) {
        flag = 128;
        if (!len || value[len - 1] != 0)
            return false;
        for (uint32_t i = 0; i < len;) {
            if (equal(value + i, len - i, "arm,pl011")) n->kind |= UART;
            if (equal(value + i, len - i, "arm,pl031")) n->kind |= RTC;
            if (equal(value + i, len - i, "arm,gic-v3")) n->kind |= GIC;
            if (equal(value + i, len - i, "arm,armv8-timer")) n->kind |= TIMER;
            while (i < len && value[i]) ++i;
            ++i;
        }
    } else if (equal(name, 32, "device_type")) {
        flag = 256;
        if (!len || value[len - 1] != 0)
            return false;
        if (equal(value, len, "memory")) n->kind |= MEMORY;
    } else if (equal(name, 32, "status")) {
        flag = 512;
        if (!len || value[len - 1] != 0)
            return false;
        if (!equal(value, len, "okay") && !equal(value, len, "ok"))
            n->enabled = false;
    } else if (n->chosen && equal(name, 32, "bootargs")) {
        flag = 1024;
        if (!len || len > 64 || value[len - 1] != 0)
            return false;
        for (uint32_t i = 0; i + 1 < len; ++i)
            if (!value[i])
                return false;
        n->bootargs = value;
        n->bootargs_len = len;
    } else if (equal(name, 32, "#interrupt-cells")) {
        flag = 2048;
        if (len != 4)
            return false;
        n->interrupt_cells = be32(value);
    } else if (equal(name, 32, "interrupt-controller")) {
        flag = 4096;
        if (len)
            return false;
        n->interrupt_controller = true;
    }
    if (n->properties & flag)
        return false;
    n->properties |= flag;
    return true;
}

int platform_parse(const void *blob, size_t available, struct platform *out)
{
    if (!blob || !out || available < 40)
        return -1;
    const uint8_t *b = blob;
    uint32_t total = be32(b + 4), so = be32(b + 8), st = be32(b + 12);
    uint32_t ro = be32(b + 16), ss = be32(b + 32), sn = be32(b + 36);
    if (be32(b) != 0xd00dfeed || total > available || total > PLATFORM_DTB_MAX ||
        total < 40 || be32(b + 20) < 17 || be32(b + 24) > 17 ||
        !span(so, sn, total) || !span(st, ss, total) || !sn || so % 4 ||
        !span(ro, 16, total) || ro % 8 || overlap(so, sn, st, ss))
        return -1;
    for (uint32_t pos = ro;; pos += 16) {
        if (!span(pos, 16, total) || overlap(pos, 16, so, sn) || overlap(pos, 16, st, ss))
            return -1;
        uint64_t address = be64(b + pos), size = be64(b + pos + 8);
        if (!address && !size)
            break;
        if (!size || address > UINT64_MAX - size || reserved_conflicts(address, size))
            return -1;
    }
    struct platform result = {0};
    struct node stack[MAX_DEPTH];
    uint32_t pos = so, end = so + sn, seen = 0;
    uint32_t gic_parent = 0, rtc_parent = 0, timer_parent = 0;
    int depth = -1;
    bool root_seen = false, root_closed = false;
    while (pos <= end && end - pos >= 4) {
        uint32_t token = be32(b + pos);
        pos += 4;
        if (token == 1) {
            if (root_closed || depth + 1 == MAX_DEPTH)
                return -1;
            uint32_t start = pos;
            while (pos < end && b[pos]) ++pos;
            if (pos == end || (depth == -1 && (root_seen || pos != start)))
                return -1;
            bool chosen = depth == 0 && equal(b + start, pos - start + 1, "chosen");
            pos = (pos + 4) & ~3u;
            if (pos > end)
                return -1;
            struct node n = {0};
            n.chosen = chosen;
            n.enabled = depth < 0 || stack[depth].enabled;
            n.address_cells = n.parent_address_cells = depth < 0 ? 2 : stack[depth].address_cells;
            n.size_cells = n.parent_size_cells = depth < 0 ? 1 : stack[depth].size_cells;
            if (depth >= 0) {
                n.irq_parent = stack[depth].irq_parent;
                n.unsafe_address = stack[depth].unsafe_address || !direct_bus(&stack[depth], depth == 0);
                stack[depth].children = true;
            }
            stack[++depth] = n;
            root_seen = true;
        } else if (token == 2) {
            if (depth < 0 || !finish(&stack[depth], &result, &seen, &gic_parent, &rtc_parent, &timer_parent))
                return -1;
            if (--depth < 0)
                root_closed = true;
        } else if (token == 3) {
            if (depth < 0 || stack[depth].children || end - pos < 8)
                return -1;
            uint32_t len = be32(b + pos), name = be32(b + pos + 4);
            pos += 8;
            if (len > end - pos || name >= ss)
                return -1;
            uint32_t i = name;
            while (i < ss && b[st + i]) ++i;
            if (i == ss || !property(&stack[depth], b + st + name, b + pos, len))
                return -1;
            pos = (pos + len + 3) & ~3u;
            if (pos > end)
                return -1;
        } else if (token == 9) {
            if (!root_closed || depth != -1 || pos != end || (seen & (MEMORY | UART)) != (MEMORY | UART))
                return -1;
            if (((seen & RTC) && (!gic_parent || rtc_parent != gic_parent)) ||
                ((seen & TIMER) && (!gic_parent || timer_parent != gic_parent)))
                return -1;
            result.dtb_size = total;
            *out = result;
            return 0;
        } else if (token != 4) {
            return -1;
        }
    }
    return -1;
}
