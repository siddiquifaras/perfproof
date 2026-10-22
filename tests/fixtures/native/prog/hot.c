/* The workload: checksum a buffer and sort an array, in rounds, for
 * about two seconds. Prints one line so an output check has something
 * to compare. */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "work.h"

int main(int argc, char **argv) {
    double seconds = argc > 1 ? atof(argv[1]) : 2.0;
    enum { BYTES = 1 << 20, INTS = 2000 };
    static uint8_t buffer[BYTES];
    static int values[INTS];
    uint64_t total = 0;
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    unsigned seed = 12345;
    do {
        for (size_t i = 0; i < BYTES; i++) {
            seed = seed * 1103515245u + 12345u;
            buffer[i] = (uint8_t)(seed >> 16);
        }
        for (int r = 0; r < 4; r++) {
            total ^= checksum(buffer, BYTES);
        }
        for (size_t i = 0; i < INTS; i++) {
            seed = seed * 1103515245u + 12345u;
            values[i] = (int)(seed >> 8) & 0xffff;
        }
        sort_ints(values, INTS);
        total ^= (uint64_t)values[INTS / 2];
        clock_gettime(CLOCK_MONOTONIC, &now);
    } while ((now.tv_sec - start.tv_sec) +
                 (now.tv_nsec - start.tv_nsec) / 1e9 <
             seconds);
    printf("total %llu\n", (unsigned long long)total);
    return 0;
}
