#ifndef WORK_H
#define WORK_H

#include <stddef.h>
#include <stdint.h>

uint64_t checksum(const uint8_t *data, size_t n);
void sort_ints(int *values, size_t n);
int never_called(int x);

#endif
