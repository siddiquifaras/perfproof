using System.Diagnostics;
using System.Runtime.InteropServices;
using Prog;

// One process of the warm-up curve: run one iteration of the fixture
// workload many times and print each iteration's time in nanoseconds,
// one per line, after a header line naming the runtime and the tiered
// compilation setting.
var iterations = args.Length > 0 ? int.Parse(args[0]) : 1500;
var tiered = Environment.GetEnvironmentVariable("DOTNET_TieredCompilation") == "0" ? "off" : "on";
Console.WriteLine($"# {RuntimeInformation.FrameworkDescription} {RuntimeInformation.RuntimeIdentifier} tiered={tiered}");
long sink = 0;
var times = new long[iterations];
for (var i = 0; i < iterations; i++)
{
    var start = Stopwatch.GetTimestamp();
    sink += Work.JoinSlow(600).Length;
    sink += Work.Histogram(36_000).Count;
    sink += Work.Checksum(90_000);
    times[i] = (Stopwatch.GetTimestamp() - start) * 1_000_000_000L / Stopwatch.Frequency;
}
foreach (var t in times)
{
    Console.WriteLine(t);
}
Console.Error.WriteLine(sink);
