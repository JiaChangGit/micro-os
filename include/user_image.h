#ifndef MICRO_USER_IMAGE_H
#define MICRO_USER_IMAGE_H
#include <stddef.h>
#include <stdint.h>

enum user_segment_flags { USER_X = 1, USER_W = 2, USER_R = 4 };
struct user_segment {
    uint64_t va, mem_size, file_size;
    uint32_t flags;
    const unsigned char *data;
};
struct user_image {
    uint64_t entry;
    size_t segment_count;
    const struct user_segment *segments;
};
#endif
