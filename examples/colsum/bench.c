/*
 * Benchmark harness for column_sums.
 *
 * usage: colsum ROWS COLS OUTPUT
 *
 * Fills a ROWS x COLS matrix of doubles from a fixed-seed generator,
 * times one column_sums call with CLOCK_MONOTONIC, writes the sums to
 * OUTPUT one per line with 17 significant digits, and prints
 * PERFPROOF {"value": <seconds>} on stdout.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "kernel.h"

static double seconds_now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static void fill(double *matrix, size_t count)
{
    uint64_t state = UINT64_C(20261016);
    for (size_t i = 0; i < count; i++) {
        state = state * UINT64_C(6364136223846793005)
                + UINT64_C(1442695040888963407);
        matrix[i] = (double)(state >> 11) * 0x1.0p-53;
    }
}

static int parse_size(const char *text, size_t *out)
{
    char *end = NULL;
    unsigned long long value = strtoull(text, &end, 10);
    if (end == text || *end != '\0' || value == 0 || value > 65536) {
        return -1;
    }
    *out = (size_t)value;
    return 0;
}

int main(int argc, char **argv)
{
    size_t rows = 0;
    size_t cols = 0;
    if (argc != 4 || parse_size(argv[1], &rows) != 0
        || parse_size(argv[2], &cols) != 0) {
        fprintf(stderr, "usage: %s ROWS COLS OUTPUT (sizes 1..65536)\n",
                argv[0]);
        return 2;
    }
    double *matrix = malloc(rows * cols * sizeof *matrix);
    double *sums = malloc(cols * sizeof *sums);
    if (matrix == NULL || sums == NULL) {
        fprintf(stderr, "out of memory\n");
        return 1;
    }
    fill(matrix, rows * cols);

    double start = seconds_now();
    column_sums(matrix, rows, cols, sums);
    double elapsed = seconds_now() - start;

    FILE *out = fopen(argv[3], "w");
    if (out == NULL) {
        perror(argv[3]);
        return 1;
    }
    for (size_t c = 0; c < cols; c++) {
        fprintf(out, "%.17g\n", sums[c]);
    }
    if (fclose(out) != 0) {
        perror(argv[3]);
        return 1;
    }
    printf("PERFPROOF {\"value\": %.9f}\n", elapsed);
    free(sums);
    free(matrix);
    return 0;
}
