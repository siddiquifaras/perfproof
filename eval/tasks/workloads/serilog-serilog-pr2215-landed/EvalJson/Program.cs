using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Serilog.Events;
using Serilog.Formatting.Json;
using Serilog.Parsing;

// Workload for serilog/serilog pull request 2215 (JsonFormatter writes
// the trace and span ids of an event as quoted strings directly): format
// 20,000 events that carry both ids to JSON. A fixed 5 warm-up and 7
// timed iterations, then the median per event; it never fails on how it
// settles. Run on the tree where the change is already in.
const int Events = 20_000;
const int Warmup = 5;
const int Timed = 7;

var template = new MessageTemplateParser().Parse("Handled {Count} items for {User}");
var traceId = ActivityTraceId.CreateFromString("4bf92f3577b34da6a3ce929d0e0e4736".AsSpan());
var spanId = ActivitySpanId.CreateFromString("00f067aa0ba902b7".AsSpan());
var start = new DateTimeOffset(2026, 1, 28, 1, 44, 31, TimeSpan.Zero);
var events = new LogEvent[Events];
for (var i = 0; i < Events; i++)
{
    events[i] = new LogEvent(
        start.AddMilliseconds(i),
        LogEventLevel.Information,
        null,
        template,
        new[]
        {
            new LogEventProperty("Count", new ScalarValue(i)),
            new LogEventProperty("User", new ScalarValue("user" + (i % 50).ToString(CultureInfo.InvariantCulture))),
        },
        traceId,
        spanId);
}
var formatter = new JsonFormatter();
var output = new StringWriter(new StringBuilder(Events * 160));

void Run()
{
    output.GetStringBuilder().Clear();
    foreach (var e in events)
    {
        formatter.Format(e, output);
    }
}

for (var i = 0; i < Warmup; i++)
{
    Run();
}
var times = new double[Timed];
for (var i = 0; i < Timed; i++)
{
    var begun = Stopwatch.GetTimestamp();
    Run();
    times[i] = Stopwatch.GetElapsedTime(begun).TotalSeconds / Events;
}
Array.Sort(times);
var json = output.ToString();
var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(json)));
Console.WriteLine($"json bytes {json.Length} sha256 {digest}");
Console.WriteLine($"PERFPROOF {{\"value\": {times[Timed / 2].ToString("R", CultureInfo.InvariantCulture)}}}");
