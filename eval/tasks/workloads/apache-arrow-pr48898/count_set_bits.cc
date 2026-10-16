// Counts the set bits of fixed bitmaps with arrow::internal::CountSetBits,
// the function the change unrolls, at the sizes of the benchmark it adds
// (16, 1024 and 131072 bytes) and at an unaligned offset. Prints the
// checksum so both trees can be compared for equal output.
#include <cstdint>
#include <iostream>
#include <vector>

#include "arrow/util/bitmap_ops.h"

int main() {
  std::vector<uint8_t> bits(131072);
  uint32_t state = 12345;
  for (auto& b : bits) {
    state = state * 1664525u + 1013904223u;
    b = static_cast<uint8_t>(state >> 24);
  }
  int64_t total = 0;
  const int64_t sizes[] = {16, 1024, 131072};
  for (int64_t bytes : sizes) {
    const int64_t reps = 400000000 / (bytes + 64);
    for (int64_t r = 0; r < reps; ++r) {
      total += arrow::internal::CountSetBits(bits.data(), r & 7, bytes * 8 - 8);
    }
  }
  std::cout << total << "\n";
  return 0;
}
