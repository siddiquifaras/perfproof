// Serialize a document of many floating-point numbers, repeatedly: the
// path that https://github.com/simdjson/simdjson/pull/2805 changes
// (internal::to_chars, called for every double the DOM serializer
// writes). Fixed work, no timer; the numbers come from a fixed seed.
//
//   c++ -std=c++20 -O2 -Iinclude -Isrc -o eval_to_chars \
//     eval_to_chars.cpp src/simdjson.cpp
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>

#include "simdjson.h"

int main() {
  const int numbers = 100000;
  const int rounds = 400;
  uint64_t state = 88172645463325252ULL;
  std::string text = "[";
  for (int i = 0; i < numbers; i++) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    double mantissa = static_cast<double>(state >> 11) / 9007199254740992.0;
    int exponent = static_cast<int>((state >> 3) % 40) - 20;
    char buffer[40];
    std::snprintf(buffer, sizeof buffer, "%s%.15fe%d", i ? "," : "",
                  mantissa, exponent);
    text += buffer;
  }
  text += "]";
  simdjson::dom::parser parser;
  simdjson::dom::element doc;
  if (parser.parse(text).get(doc)) {
    std::fprintf(stderr, "the generated document does not parse\n");
    return 2;
  }
  size_t total = 0;
  for (int r = 0; r < rounds; r++) {
    total += simdjson::to_string(doc).size();
  }
  std::printf("%d numbers, %d rounds, %zu bytes written\n", numbers, rounds,
              total);
  return 0;
}
