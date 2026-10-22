/* Time blocks of the lz4 decompression workload, for the native warm-up
 * study (docs/method.md "Warm-up (C and C++)"). One process prints, for
 * each of 60 blocks of 300 decompressions of the same 370 KB block, the
 * mean nanoseconds a decompression took. The same input and the same
 * LZ4_decompress_safe call as
 * eval/tasks/workloads/lz4-lz4-pr1040/eval_decode.c, timed in blocks
 * (without that program's per-round comparison of the output).
 *
 *   cc -O2 -Ilib -o blocks blocks.c lib/lz4.c
 *   ./blocks lib/lz4hc.c lib/lz4frame.c programs/lz4io.c tests/fuzzer.c \
 *     tests/frametest.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "lz4.h"

enum { BLOCKS = 60, PER_BLOCK = 300, MAX_INPUT = 1 << 20 };

static double now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e9 + t.tv_nsec;
}

int main(int argc, char **argv) {
    char *input = malloc(MAX_INPUT);
    size_t size = 0;
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "rb");
        if (f == NULL) {
            return 2;
        }
        size += fread(input + size, 1, MAX_INPUT - size, f);
        fclose(f);
    }
    int bound = LZ4_compressBound((int)size);
    char *packed = malloc((size_t)bound);
    char *output = malloc(size);
    int packed_size = LZ4_compress_default(input, packed, (int)size, bound);
    unsigned long long sum = 0;
    for (int b = 0; b < BLOCKS; b++) {
        double start = now_ns();
        for (int r = 0; r < PER_BLOCK; r++) {
            LZ4_decompress_safe(packed, output, packed_size, (int)size);
            sum += (unsigned char)output[r % size];
        }
        printf("block %d %.1f\n", b, (now_ns() - start) / PER_BLOCK);
    }
    fprintf(stderr, "check %llu\n", sum);
    return 0;
}
