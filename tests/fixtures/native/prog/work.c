#include "work.h"

/* FNV-1a: one multiply per byte, the loop the profile should name. */
uint64_t checksum(const uint8_t *data, size_t n) {
    uint64_t h = 1469598103934665603ULL;
    for (size_t i = 0; i < n; i++) {
        h ^= data[i];
        h *= 1099511628211ULL;
    }
    return h;
}

/* Insertion sort: quadratic on purpose, so it shows up next to the
 * checksum in a profile. */
void sort_ints(int *values, size_t n) {
    for (size_t i = 1; i < n; i++) {
        int v = values[i];
        size_t j = i;
        while (j > 0 && values[j - 1] > v) {
            values[j] = values[j - 1];
            j--;
        }
        values[j] = v;
    }
}

int never_called(int x) {
    if (x > 0) {
        return x * 2;
    }
    return -x;
}
