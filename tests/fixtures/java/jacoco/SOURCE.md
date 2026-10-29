# JaCoCo XML fixture

`report.xml` is the untouched output of `jacococli report`, captured on
2026-10-29 on macOS arm64 (M4 MacBook Pro).

- JDK: Eclipse Temurin 21.0.12.1+1.
- JaCoCo 0.8.13 from Maven Central: `org.jacoco.agent-0.8.13-runtime.jar`
  (sha256 `47e700ccb0fdb9e27c5241353f8161938f4e53c3561dd35e063c5fe88dc3349b`)
  and `org.jacoco.cli-0.8.13-nodeps.jar` (sha256
  `8f748683833d4dc4d72cea5d6b43f49344687b831e0582c97bcb9b984e3de0a3`).
- Test runner: `junit-platform-console-standalone-1.11.4.jar` from Maven
  Central.
- Project: `Calc.java` and `CalcTest.java` beside this file, laid out
  as `src/main/java/com/example/Calc.java` and
  `src/test/java/com/example/CalcTest.java`. `Calc.add` is tested;
  `Calc.unused` (line 12) is not.

Commands, run in the project root (the report path is the one a
perfproof coverage command receives):

```
javac -d out/main src/main/java/com/example/Calc.java
javac -cp junit-platform-console-standalone-1.11.4.jar:out/main \
  -d out/test src/test/java/com/example/CalcTest.java
JAVA_TOOL_OPTIONS=-javaagent:org.jacoco.agent-0.8.13-runtime.jar=destfile=jacoco.exec \
  java -jar junit-platform-console-standalone-1.11.4.jar execute \
  --class-path out/main:out/test --scan-class-path out/test
java -jar org.jacoco.cli-0.8.13-nodeps.jar report jacoco.exec \
  --classfiles out/main --sourcefiles src/main/java --xml report.xml
```

(The capture ran the last two commands through the `Coverage.java`
wrapper shown in `skills/perfproof/java.md`, which sets
`JAVA_TOOL_OPTIONS` as above and puts the XML at the report path.) The report lists lines 7 and 8 with `ci` 4 and 2 and line 12
with `ci` 0.
