#ifndef COLSUM_KERNEL_H
#define COLSUM_KERNEL_H

#include <stddef.h>

/*
 * Sum each column of a row-major rows x cols matrix into sums[cols].
 * Every column is summed from row 0 down, so any correct version gives
 * bit-identical sums.
 */
void column_sums(const double *matrix, size_t rows, size_t cols,
                 double *sums);

#endif
