# .NET and Ruby task support

Six manifests for the Ruby and C# task (T5.14), three a language, each
with a workload that is a program run in a container and an oracle that
is the project's own tests.

| Manifest | Family | Workload |
|---|---|---|
| `rails-rails-pr58964` | known-change | 300,000 `EventReporter#notify` calls under YJIT (inline in `eval/mine/checks.json`) |
| `rails-rails-pr59030` | known-change, used as a find task | 200,000 passes of `String#blank?` over four strings under YJIT (inline in `checks.json`) |
| `rails-rails-pr58751-landed` | nothing-to-keep | `TimeWithZone#xmlschema` and `#to_s` under YJIT (inline in `checks.json`) |
| `xoofx-markdig-pr922` | hidden-fix | parse one pipe table of 600 rows (`xoofx-markdig-pr922/EvalPipeTable`) |
| `xoofx-markdig-pr866` | known-change | parse a document of many links and ten alert blocks (`xoofx-markdig-pr866/EvalAlerts`) |
| `serilog-serilog-pr2215-landed` | nothing-to-keep | format 20,000 events with trace and span ids to JSON (`serilog-serilog-pr2215-landed/EvalJson`) |

Their workloads and oracles ran on both trees with `eval/mine/check.py`
(status `runs`), through Docker: the Ruby ones in the official `ruby:4.0`
image plus tzdata-legacy, the C# ones in the official
`mcr.microsoft.com/dotnet/sdk:10.0` image with NuGet packages in the
`perfproof-nuget` volume. The commands and the output tails are in each
manifest's `runs`. The three Markdig and Serilog manifests are new:
**no C# repository was in the pool**, so they are written by hand, as
the Java ones were, with a new target class `dotnet`
(`eval/mine/task.py`, `draw.py`) that `draw.py` has no picks for yet
(`SPLITS.md`: 0 of 5 slots filled).

**What the Docker runs mean for the verdict.** `docker run ... dotnet`
and `docker run ... bundle` are launchers: the runtime gate reports
`runtime` `not_available` for them, so a comparison through these
commands can measure but never KEEP. The C# task file
(`eval/skill/tasks-dotnet.json`) therefore asks for a native `dotnet`
on the PATH, as the Go task file asks for native Go, and its tasks skip
with the reason where there is none (this Mac has none). The Ruby task
file (`tasks-ruby.json`) uses the container launchers the earlier Rails
tasks use, so its KEEP is withheld the same way; a native Ruby new enough
for Rails (4.0 here, the Mac's system Ruby is 2.6) would remove that, and
installing one is the owner's decision.

The Markdig tests are built for `net8.0;net9.0` and the image holds only
the .NET 10 runtime, so the oracle runs `-f net9.0` with
`DOTNET_ROLL_FORWARD=Major`. Serilog's test project restores with
warnings as errors, and its pinned packages have known advisories that
the 10.0.402 SDK reports (NU1903), so its oracle passes
`-p:NuGetAudit=false`; the manifest's `runs` records the command.

The workload runs are single runs on a Mac that other work was using,
and no decision used them. `rails-rails-pr59030` and the three C#
manifests are `development-candidate`, and `realrepo.load_spec` accepts
only `development` ones, so the task files load with `taskset.load`
(checked by `eval/skill/test_tasks_ruby_dotnet.py`) and their tasks run
once they are drawn into development or the loader admits candidates
that are not held-out; neither is done here. None of the six tasks has
been run through the skill.

`studies/ruby/` and `studies/dotnet/` hold the profiles of the Rails
`String#blank?` and Markdig pipe-table workloads through the wrappers.
