# Skill eval `r8-3-java`

Skill ``, Claude Code .

- Tasks passed: 0 of 0
- Trigger set: 0 of 0 matched
- Verdict accepted: 0 of 0; wrong KEEP: 0
- Result line correct: 0 of 0
- Hotspot in a real `perfproof profile` output and named: no task has one
- Fix passes tests, no locked file touched (prove: no code changed): 0 of 0
- Tree clean: 0 of 0
- Wording violations: none
- Cost $0.00, 0 agent turns, 0s of `claude` wall time (0s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

## Skipped (not scored)

- `java-guava-known-change`: setup `./mvnw -q -B -pl guava -am -DskipTests -Dmaven.javadoc.skip=true -Dmaven.source.` exited 1: 	at java.base/sun.nio.fs.UnixFileSystemProvider.createDirectory(UnixFileSystemProvider.java:462)
	at java.base/java.nio.file.Files.createDirectory(Files.java:700)
	at java.base/java.nio.file.Files.createAndCheckIsDirectory(Files.java:808)
	at java.base/java.nio.file.Files.createDirectories(Files.java:794)
	at org.apache.maven.wrapper.DefaultDownloader.download(DefaultDownloader.java:80)
	at org.apache.maven.wrapper.Installer.createDist(Installer.java:84)
	at org.apache.maven.wrapper.WrapperExecutor.execute(WrapperExecutor.java:160)
	at org.apache.maven.wrapper.MavenWrapperMain.main(MavenWrapperMain.java:73)
- `java-jackson-core-hidden-fix`: setup `./mvnw -q -B -DskipTests -Dmaven.javadoc.skip=true -Dmaven.source.skip=true pack` exited 1: [ERROR] Could not create local repository at /Users/ppeval/.m2/repository -> [Help 1]
[ERROR] 
[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.
[ERROR] Re-run Maven using the -X switch to enable full debug logging.
[ERROR] 
[ERROR] For more information about the errors and possible solutions, please read the following articles:
[ERROR] [Help 1] http://cwiki.apache.org/confluence/display/MAVEN/LocalRepositoryNotAccessibleException
- `java-jackson-core-nothing-to-keep`: setup `./mvnw -q -B -DskipTests -Dmaven.javadoc.skip=true -Dmaven.source.skip=true pack` exited 1: [ERROR] Could not create local repository at /Users/ppeval/.m2/repository -> [Help 1]
[ERROR] 
[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.
[ERROR] Re-run Maven using the -X switch to enable full debug logging.
[ERROR] 
[ERROR] For more information about the errors and possible solutions, please read the following articles:
[ERROR] [Help 1] http://cwiki.apache.org/confluence/display/MAVEN/LocalRepositoryNotAccessibleException
