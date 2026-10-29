# Skill eval `r10-java`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 2 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top15 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 3 of 3
- Tree clean: 3 of 3
- Wording violations: none
- Cost $2.11, 69 agent turns, 844s of `claude` wall time (837s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| java-jackson-core-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 24 | $0.79 | 458s | 3.9 |
| java-guava-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 18 | $0.39 | 220s | 5.0 |
| java-jackson-core-nothing-to-keep | ok | - | none | ok | - | ok | ok | ok | 27 | $0.93 | 165s | 4.3 |

## Failures

- `java-jackson-core-hidden-fix`
  - hotspot: encodeBase64Chunk is not in the first 15 of hotspots or frames_by_total of a saved profile: ['tools.jackson.core.json.UTF8JsonGenerator._writeStringSegment', 'Bench.iteration', 'tools.jackson.core.json.UTF8StreamJsonParser._skipWSOrEnd', 'tools.jackson.core.json.UTF8JsonGenerator.writeRaw', 'jdk.internal.math.DoubleToDecimal.toDecimal', 'tools.jackson.core.base.ParserMinimalBase._updateToken', 'tools.jackson.core.json.UTF8JsonGenerator._writeStringSegment', 'tools.jackson.core.json.UTF8JsonGenerator._verifyValueWrite', 'jdk.internal.math.DoubleToDecimal.appendDigit', 'java.lang.StringLatin1.charAt', 'tools.jackson.core.util.TextBuffer.emptyAndGetCurrentSegment', 'tools.jackson.core.json.JsonReadContext.expectComma', 'tools.jackson.core.json.UTF8StreamJsonParser._parseUnsignedNumber', 'tools.jackson.core.json.JsonWriteContext.writeName', 'Bench.iteration', 'Bench.iteration', 'tools.jackson.core.json.UTF8StreamJsonParser.nextToken', 'tools.jackson.core.json.UTF8JsonGenerator.writeNumber', 'tools.jackson.core.json.UTF8JsonGenerator._writeStringSegment', 'tools.jackson.core.json.UTF8JsonGenerator.writeString', 'tools.jackson.core.json.UTF8JsonGenerator.writeName', 'tools.jackson.core.json.UTF8StreamJsonParser.getString', 'tools.jackson.core.json.UTF8StreamJsonParser._finishAndReturnString', 'tools.jackson.core.json.JsonParserBase._parseNumericValue', 'tools.jackson.core.json.UTF8StreamJsonParser._parseUnsignedNumber', 'jdk.internal.math.DoubleToDecimal.toString', 'tools.jackson.core.io.NumberOutput.toString', 'java.lang.Double.toString', 'jdk.internal.math.DoubleToDecimal.toDecimalString', 'jdk.internal.math.DoubleToDecimal.toDecimal', 'java.lang.String.encodeUTF8', 'tools.jackson.core.json.WriterBasedJsonGenerator._writeString2', 'tools.jackson.core.json.WriterBasedJsonGenerator._writeString2', 'java.lang.AbstractStringBuilder.appendChars', 'java.lang.StringUTF16.compress', 'java.lang.AbstractStringBuilder.appendChars', 'java.lang.StringLatin1.getChars', 'java.io.Writer.<init>', 'tools.jackson.core.sym.CharsToNameCanonicalizer.findSymbol', 'tools.jackson.core.json.ReaderBasedJsonParser.nextToken', 'tools.jackson.core.base.ParserMinimalBase._updateToken', 'BenchChars.iteration', 'tools.jackson.core.json.ReaderBasedJsonParser._parseUnsignedNumber', 'tools.jackson.core.io.NumberOutput._leading3', 'tools.jackson.core.json.ReaderBasedJsonParser._parseName', 'BenchChars.iteration', 'tools.jackson.core.json.ReaderBasedJsonParser.nextToken', 'tools.jackson.core.json.WriterBasedJsonGenerator._writeString', 'tools.jackson.core.json.WriterBasedJsonGenerator.writeString', 'tools.jackson.core.json.WriterBasedJsonGenerator._writeString2', 'tools.jackson.core.json.WriterBasedJsonGenerator.writeNumber', 'java.lang.String.encodeUTF8', 'tools.jackson.core.json.ReaderBasedJsonParser.getString', 'tools.jackson.core.json.ReaderBasedJsonParser._parseUnsignedNumber', 'tools.jackson.core.json.ReaderBasedJsonParser._parseName', 'java.lang.StringBuffer.append', 'java.io.StringWriter.write', 'tools.jackson.core.json.ReaderBasedJsonParser._finishString', 'tools.jackson.core.json.WriterBasedJsonGenerator.writeName', 'tools.jackson.core.json.WriterBasedJsonGenerator._writeName']
