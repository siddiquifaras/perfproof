/* Deterministic CPU-bound native target for the golden set: generate
 * keys from an integer LCG, sort them and print a checksum of the order.
 * Sized to about 0.1 s of work. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define N 600000

static int cmp(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

int main(void) {
    uint64_t *keys = malloc(sizeof(uint64_t) * N);
    uint64_t state = 12345;
    for (int i = 0; i < N; i++) {
        state = (state * 1103515245ULL + 12345ULL) & 0xffffffffULL;
        keys[i] = (state << 20) | (uint64_t)(i & 0xfffff);
    }
    qsort(keys, N, sizeof(uint64_t), cmp);
    uint64_t sum = 0;
    for (int i = 0; i < N; i++) {
        sum = (sum * 31 + (keys[i] & 0xfffff)) & 0xffffffffULL;
    }
    printf("checksum %llu\n", (unsigned long long)sum);
    free(keys);
    return 0;
}
