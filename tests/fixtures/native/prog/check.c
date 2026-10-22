/* The correctness check: runs checksum and sort_ints, not never_called,
 * so a coverage report has one function with no executed line. */
#include <stdio.h>

#include "work.h"

int main(void) {
    const uint8_t data[] = {1, 2, 3, 4};
    int values[] = {3, 1, 2};
    if (checksum(data, sizeof data) == 0) {
        return 1;
    }
    sort_ints(values, 3);
    if (values[0] != 1 || values[2] != 3) {
        return 1;
    }
    puts("ok");
    return 0;
}
