# Skill eval `r10-native`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 2 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top15 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: none
- Cost $2.35, 90 agent turns, 861s of `claude` wall time (856s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| native-simdjson-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 43 | $1.24 | 345s | 4.3 |
| native-lz4-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 17 | $0.30 | 249s | 5.3 |
| native-zstd-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 30 | $0.81 | 268s | 5.5 |

## Failures

- `native-simdjson-hidden-fix`
  - hotspot: simdjson::internal::to_chars is not in the first 15 of hotspots or frames_by_total of a saved profile: ['simdjson::arm64::dom_parser_implementation::stage1(unsigned char const*, unsigned long, simdjson::stage1_mode)', 'simdjson::arm64::dom_parser_implementation::stage2(simdjson::dom::document&)', 'main', 'simdjson::arm64::implementation::minify(unsigned char const*, unsigned long, unsigned char*, unsigned long&) const', 'walk(simdjson::dom::element)', 'walk(simdjson::dom::element)', 'simdjson::arm64::implementation::validate_utf8(char const*, unsigned long) const', 'bench`walk(simdjson::dom::element)', 'walk(simdjson::dom::element)', 'walk(simdjson::dom::element)', 'walk(simdjson::dom::element)', 'bench`simdjson::arm64::dom_parser_implementation::stage2(simdjson::dom::document&)', 'libsystem_kernel.dylib`madvise', 'main', 'simdjson::arm64::dom_parser_implementation::stage1(unsigned char const*, unsigned long, simdjson::stage1_mode)', 'simdjson::arm64::dom_parser_implementation::parse(unsigned char const*, unsigned long, simdjson::dom::document&)', 'simdjson::arm64::dom_parser_implementation::stage2(simdjson::dom::document&)', 'simdjson::arm64::implementation::minify(unsigned char const*, unsigned long, unsigned char*, unsigned long&) const', 'walk(simdjson::dom::element)', 'simdjson::arm64::implementation::validate_utf8(char const*, unsigned long) const', 'bench`walk(simdjson::dom::element)', 'bench`simdjson::arm64::dom_parser_implementation::stage2(simdjson::dom::document&)', 'libsystem_kernel.dylib`madvise', 'libsystem_platform.dylib`_platform_memmove', 'simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'simdjson::arm64::dom_parser_implementation::stage1(unsigned char const*, unsigned long, simdjson::stage1_mode)', 'simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'od_walk(simdjson::arm64::ondemand::value)', 'simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'od_walk(simdjson::arm64::ondemand::value)', 'od_walk(simdjson::arm64::ondemand::value)', 'bench2`simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'bench2`DYLD-STUB$$memmove', 'od_walk(simdjson::arm64::ondemand::value)', 'simdjson::arm64::dom_parser_implementation::parse_string(unsigned char const*, unsigned char*, bool) const', 'od_walk(simdjson::arm64::ondemand::value)', 'simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'libsystem_platform.dylib`_platform_memmove', 'od_walk(simdjson::arm64::ondemand::value)', 'simdjson::arm64::dom_parser_implementation::stage1(unsigned char const*, unsigned long, simdjson::stage1_mode)', 'bench2`simdjson::internal::string_builder<simdjson::internal::mini_formatter>::append(simdjson::dom::element)', 'libc++.1.dylib`std::basic_string<char>::push_back(char)', 'bench2`DYLD-STUB$$memmove', 'simdjson::arm64::dom_parser_implementation::parse_string(unsigned char const*, unsigned char*, bool) const', 'libsystem_platform.dylib`__bzero', 'libsystem_kernel.dylib`madvise', 'bench2`DYLD-STUB$$memcpy', 'libsystem_platform.dylib`_platform_memset']
