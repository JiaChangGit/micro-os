#include "platform.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct fixture {
    unsigned char tree[4096], strings[512], blob[8192];
    size_t tree_size, strings_size, blob_size;
};

static void put32(unsigned char *p, uint32_t v)
{
    p[0] = (unsigned char)(v >> 24);
    p[1] = (unsigned char)(v >> 16);
    p[2] = (unsigned char)(v >> 8);
    p[3] = (unsigned char)v;
}

static uint32_t get32(const unsigned char *p)
{
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static void word(struct fixture *f, uint32_t v)
{
    assert(f->tree_size + 4 <= sizeof(f->tree));
    put32(f->tree + f->tree_size, v);
    f->tree_size += 4;
}

static void begin(struct fixture *f, const char *name)
{
    word(f, 1);
    size_t n = strlen(name) + 1;
    memcpy(f->tree + f->tree_size, name, n);
    f->tree_size = (f->tree_size + n + 3) & ~(size_t)3;
}

static void prop(struct fixture *f, const char *name, const void *value, size_t size)
{
    word(f, 3);
    word(f, (uint32_t)size);
    word(f, (uint32_t)f->strings_size);
    size_t n = strlen(name) + 1;
    memcpy(f->strings + f->strings_size, name, n);
    f->strings_size += n;
    if (size)
        memcpy(f->tree + f->tree_size, value, size);
    f->tree_size = (f->tree_size + size + 3) & ~(size_t)3;
}

static void number(struct fixture *f, const char *name, uint32_t value)
{
    unsigned char bytes[4];
    put32(bytes, value);
    prop(f, name, bytes, sizeof(bytes));
}

static void reg(struct fixture *f, uint64_t address, uint64_t size)
{
    unsigned char bytes[16];
    put32(bytes, (uint32_t)(address >> 32));
    put32(bytes + 4, (uint32_t)address);
    put32(bytes + 8, (uint32_t)(size >> 32));
    put32(bytes + 12, (uint32_t)size);
    prop(f, "reg", bytes, sizeof(bytes));
}

static void uart(struct fixture *f)
{
    begin(f, "serial@9000000");
    prop(f, "compatible", "arm,pl011", 10);
    reg(f, 0x09000000, 0x1000);
    word(f, 2);
}

static void pack(struct fixture *f)
{
    put32(f->blob, 0xd00dfeed);
    put32(f->blob + 8, 56);
    put32(f->blob + 12, (uint32_t)(56 + f->tree_size));
    put32(f->blob + 16, 40);
    put32(f->blob + 20, 17);
    put32(f->blob + 24, 16);
    put32(f->blob + 32, (uint32_t)f->strings_size);
    put32(f->blob + 36, (uint32_t)f->tree_size);
    memcpy(f->blob + 56, f->tree, f->tree_size);
    memcpy(f->blob + 56 + f->tree_size, f->strings, f->strings_size);
    f->blob_size = 56 + f->tree_size + f->strings_size;
    put32(f->blob + 4, (uint32_t)f->blob_size);
}

static void fixture(struct fixture *f, int translated_uart)
{
    memset(f, 0, sizeof(*f));
    begin(f, "");
    number(f, "#address-cells", 2);
    number(f, "#size-cells", 2);
    begin(f, "memory@40000000");
    prop(f, "device_type", "memory", 7);
    reg(f, 0x40000000, 0x20000000);
    word(f, 2);
    if (translated_uart) {
        begin(f, "soc");
        number(f, "#address-cells", 2);
        number(f, "#size-cells", 2);
        unsigned char ranges[24] = {0};
        put32(ranges + 12, translated_uart == 1 ? 0x1000 : 0);
        put32(ranges + 20, translated_uart == 2 ? 0x1000 : 0x10000000);
        prop(f, "ranges", ranges, translated_uart == 3 ? 0 : sizeof(ranges));
    }
    uart(f);
    if (translated_uart)
        word(f, 2);
    word(f, 2);
    word(f, 9);
    pack(f);
}

static void invalid_unchanged(const void *data, size_t size)
{
    struct platform result, before;
    memset(&result, 0xa5, sizeof(result));
    memcpy(&before, &result, sizeof(result));
    assert(platform_parse(data, size, &result) == -1);
    assert(memcmp(&before, &result, sizeof(result)) == 0);
}

static void test_fixture(void)
{
    struct fixture f;
    struct platform p;
    fixture(&f, 0);
    assert(platform_parse(f.blob, f.blob_size, &p) == 0);
    assert(p.ram_base == 0x40000000 && p.ram_size == 0x20000000);
    assert(p.uart_base == 0x09000000 && p.rtc_base == 0 && p.dtb_size == f.blob_size);
    for (size_t length = 0; length < f.blob_size; ++length)
        invalid_unchanged(f.blob, length);
    invalid_unchanged(NULL, f.blob_size);
    assert(platform_parse(f.blob, f.blob_size, NULL) == -1);

    const uint32_t offsets[] = {0, 4, 8, 12, 16, 24, 32, 36};
    for (size_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]); ++i) {
        fixture(&f, 0);
        put32(f.blob + offsets[i], UINT32_MAX);
        invalid_unchanged(f.blob, f.blob_size);
    }
    fixture(&f, 0);
    put32(f.blob + 20, 16);
    invalid_unchanged(f.blob, f.blob_size);
    fixture(&f, 0);
    put32(f.blob + 16, 48); /* reserve table overlaps structure */
    invalid_unchanged(f.blob, f.blob_size);
    fixture(&f, 0);
    put32(f.blob + 56, 2); /* END_NODE before root */
    invalid_unchanged(f.blob, f.blob_size);
    fixture(&f, 0);
    put32(f.blob + 56 + f.tree_size - 4, 5);
    invalid_unchanged(f.blob, f.blob_size);
    fixture(&f, 0);
    memset(f.blob + 56 + f.tree_size, 'x', f.strings_size);
    invalid_unchanged(f.blob, f.blob_size);
    fixture(&f, 1);
    invalid_unchanged(f.blob, f.blob_size);
    fixture(&f, 2); /* nonempty identity window does not cover UART */
    invalid_unchanged(f.blob, f.blob_size);
    fixture(&f, 3); /* empty ranges makes the child bus address-identical */
    assert(platform_parse(f.blob, f.blob_size, &p) == 0);
}

/* 增加一筆 reserve entry 與 terminator，並將其後的區塊往後移。 */
static void reserve_fixture(struct fixture *f, uint64_t address, uint64_t size)
{
    fixture(f, 0);
    assert(f->blob_size + 16 <= sizeof(f->blob));
    memmove(f->blob + 72, f->blob + 56, f->blob_size - 56);
    f->blob_size += 16;
    put32(f->blob + 4, (uint32_t)f->blob_size);
    put32(f->blob + 8, get32(f->blob + 8) + 16);
    put32(f->blob + 12, get32(f->blob + 12) + 16);
    put32(f->blob + 40, (uint32_t)(address >> 32));
    put32(f->blob + 44, (uint32_t)address);
    put32(f->blob + 48, (uint32_t)(size >> 32));
    put32(f->blob + 52, (uint32_t)size);
    memset(f->blob + 56, 0, 16);
}

static void test_reservations(void)
{
    struct fixture f;
    struct platform p;
    const struct { uint64_t address, size; } rejected[] = {
        {0x40080000, 1}, {0x401fffff, 1}, {0x4007ffff, 2},
        {0x41000000, 0x60000}, {0x4105ffff, 1}, {0x40ffffff, 2},
        {0x40000000, 0x1100000}, {UINT64_MAX - 1, 3}, {1, 0}
    };
    const struct { uint64_t address, size; } accepted[] = {
        {0x4007f000, 0x1000}, {0x40200000, 0x1000},
        {0x40fff000, 0x1000}, {0x41060000, 0x1000},
        {UINT64_MAX - 1, 1}
    };
    for (size_t i = 0; i < sizeof(rejected) / sizeof(rejected[0]); ++i) {
        reserve_fixture(&f, rejected[i].address, rejected[i].size);
        invalid_unchanged(f.blob, f.blob_size);
    }
    for (size_t i = 0; i < sizeof(accepted) / sizeof(accepted[0]); ++i) {
        reserve_fixture(&f, accepted[i].address, accepted[i].size);
        assert(platform_parse(f.blob, f.blob_size, &p) == 0);
    }
}

static void bootargs_fixture(struct fixture *f, const char *args, size_t size)
{
    fixture(f, 0);
    f->tree_size -= 8;
    begin(f, "chosen");
    prop(f, "bootargs", args, size);
    word(f, 2);
    word(f, 2);
    word(f, 9);
    pack(f);
}

static void test_bootargs(void)
{
    struct fixture f;
    struct platform p;
    bootargs_fixture(&f, "case=p0-boot", sizeof("case=p0-boot"));
    assert(platform_parse(f.blob, f.blob_size, &p) == 0);
    assert(strcmp(p.bootargs, "case=p0-boot") == 0);
    fixture(&f, 0);
    assert(platform_parse(f.blob, f.blob_size, &p) == 0 && !p.bootargs[0]);
    char args[65];
    memset(args, 'x', sizeof(args));
    args[63] = 0;
    bootargs_fixture(&f, args, 64);
    assert(platform_parse(f.blob, f.blob_size, &p) == 0 && strlen(p.bootargs) == 63);
    args[63] = 'x';
    bootargs_fixture(&f, args, 64);
    invalid_unchanged(f.blob, f.blob_size);
    args[64] = 0;
    bootargs_fixture(&f, args, 65);
    invalid_unchanged(f.blob, f.blob_size);
    args[63] = 0;
    args[4] = 0;
    bootargs_fixture(&f, args, 64);
    invalid_unchanged(f.blob, f.blob_size);
    bootargs_fixture(&f, "", 0);
    invalid_unchanged(f.blob, f.blob_size);
}

static void gic_fixture(struct fixture *f, uint32_t count, int marker_len, uint32_t parent)
{
    fixture(f, 0);
    f->tree_size -= 8;
    begin(f, "intc@8000000");
    prop(f, "compatible", "arm,gic-v3", sizeof("arm,gic-v3"));
    number(f, "phandle", 7);
    number(f, "#interrupt-cells", count);
    if (marker_len >= 0)
        prop(f, "interrupt-controller", "abcd", (size_t)marker_len);
    unsigned char bytes[32] = {0};
    put32(bytes + 4, 0x08000000);
    put32(bytes + 12, 0x10000);
    put32(bytes + 20, 0x080a0000);
    put32(bytes + 28, 0x20000);
    prop(f, "reg", bytes, sizeof(bytes));
    word(f, 2);
    begin(f, "rtc@9010000");
    prop(f, "compatible", "arm,pl031", sizeof("arm,pl031"));
    reg(f, 0x09010000, 0x1000);
    number(f, "interrupt-parent", parent);
    memset(bytes, 0, sizeof(bytes));
    put32(bytes + 4, 2);
    put32(bytes + 8, 4);
    prop(f, "interrupts", bytes, 12);
    word(f, 2);
    word(f, 2);
    word(f, 9);
    pack(f);
}

static void test_interrupt_schema(void)
{
    struct fixture f;
    struct platform p;
    gic_fixture(&f, 3, 0, 7);
    assert(platform_parse(f.blob, f.blob_size, &p) == 0 && p.rtc_irq == 34);
    gic_fixture(&f, 2, 0, 7);
    invalid_unchanged(f.blob, f.blob_size);
    gic_fixture(&f, 3, -1, 7);
    invalid_unchanged(f.blob, f.blob_size);
    gic_fixture(&f, 3, 4, 7);
    invalid_unchanged(f.blob, f.blob_size);
    gic_fixture(&f, 3, 0, 9);
    invalid_unchanged(f.blob, f.blob_size);
}

static void test_real(const char *path)
{
    FILE *file = fopen(path, "rb");
    assert(file);
    assert(fseek(file, 0, SEEK_END) == 0);
    long length = ftell(file);
    assert(length >= 40 && (unsigned long)length <= PLATFORM_DTB_MAX);
    rewind(file);
    unsigned char *data = malloc((size_t)length), *copy = malloc((size_t)length);
    assert(data && copy);
    assert(fread(data, 1, (size_t)length, file) == (size_t)length);
    assert(fclose(file) == 0);
    struct platform p;
    assert(platform_parse(data, (size_t)length, &p) == 0);
    assert(p.ram_base == 0x40000000 && p.ram_size == 0x20000000);
    assert(p.uart_base == 0x09000000 && p.rtc_base == 0x09010000);
    assert(p.gicd_base == 0x08000000 && p.gicr_base == 0x080a0000);
    assert(p.rtc_irq == 34 && p.timer_irq == 30);

    uint32_t structure = get32(data + 8), strings = get32(data + 12);
    uint32_t used = strings + get32(data + 32);
    for (uint32_t n = 0; n < used; n += 13) {
        memcpy(copy, data, (size_t)length);
        copy[n] ^= 0xff;
        (void)platform_parse(copy, (size_t)length, &p);
    }
    for (uint32_t n = structure; n < strings; n += 4) {
        memcpy(copy, data, (size_t)length);
        put32(copy + n, UINT32_MAX);
        (void)platform_parse(copy, (size_t)length, &p);
    }
    printf("FDT PASS ram=0x%llx uart=0x%llx rtc_irq=%u timer_irq=%u mutations=%lu\n",
           (unsigned long long)p.ram_base, (unsigned long long)p.uart_base,
           p.rtc_irq, p.timer_irq, ((unsigned long)used + 12) / 13 + (strings - structure) / 4);
    free(copy);
    free(data);
}

int main(int argc, char **argv)
{
    test_fixture();
    test_reservations();
    test_bootargs();
    test_interrupt_schema();
    if (argc == 2)
        test_real(argv[1]);
    else
        puts("FDT PASS synthetic fixtures");
    return 0;
}
