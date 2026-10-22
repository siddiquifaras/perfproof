/* Decompress the same block many times: the cost that
 * https://github.com/lz4/lz4/pull/1040 changes on Apple and AArch64
 * builds (the fast decoding loop in lib/lz4.c). Fixed work, no timer:
 * the input is source files that the pull request does not touch, so
 * both trees decompress the same bytes.
 *
 *   cc -O2 -Ilib -o eval_decode eval_decode.c lib/lz4.c
 *   ./eval_decode lib/lz4hc.c lib/lz4frame.c programs/lz4io.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lz4.h"

enum { ROUNDS = 20000, MAX_INPUT = 1 << 20 };

int main(int argc, char **argv) {
    char *input = malloc(MAX_INPUT);
    size_t size = 0;
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "rb");
        if (f == NULL) {
            fprintf(stderr, "cannot open %s\n", argv[i]);
            return 2;
        }
        size += fread(input + size, 1, MAX_INPUT - size, f);
        fclose(f);
    }
    if (size == 0) {
        return 2;
    }
    int bound = LZ4_compressBound((int)size);
    char *packed = malloc((size_t)bound);
    char *output = malloc(size);
    int packed_size = LZ4_compress_default(input, packed, (int)size, bound);
    if (packed_size <= 0) {
        return 3;
    }
    unsigned long long sum = 0;
    for (int r = 0; r < ROUNDS; r++) {
        int got = LZ4_decompress_safe(packed, output, packed_size, (int)size);
        if (got != (int)size || memcmp(input, output, size) != 0) {
            fprintf(stderr, "round %d: decompressed wrong bytes\n", r);
            return 1;
        }
        sum += (unsigned char)output[r % size];
    }
    printf("%zu -> %d bytes, %d rounds, check %llu\n", size, packed_size,
           ROUNDS, sum);
    return 0;
}
