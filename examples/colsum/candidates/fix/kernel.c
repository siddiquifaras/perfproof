#include "kernel.h"

void column_sums(const double *matrix, size_t rows, size_t cols,
                 double *sums)
{
    for (size_t c = 0; c < cols; c++) {
        sums[c] = 0.0;
    }
    for (size_t r = 0; r < rows; r++) {
        const double *row = matrix + r * cols;
        for (size_t c = 0; c < cols; c++) {
            sums[c] += row[c];
        }
    }
}
