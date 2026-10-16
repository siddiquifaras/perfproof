#include "kernel.h"

void column_sums(const double *matrix, size_t rows, size_t cols,
                 double *sums)
{
    for (size_t c = 0; c < cols; c++) {
        double total = 0.0;
        for (size_t r = 0; r < rows; r++) {
            total += matrix[r * cols + c];
        }
        sums[c] = total;
    }
}
