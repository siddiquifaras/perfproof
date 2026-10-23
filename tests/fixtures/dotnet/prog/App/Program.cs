using System.Diagnostics;
using Prog;

var seconds = args.Length > 0 ? double.Parse(args[0]) : 2.0;
var watch = Stopwatch.StartNew();
long sink = 0;
while (watch.Elapsed.TotalSeconds < seconds)
{
    sink += Work.JoinSlow(2000).Length;
    sink += Work.Histogram(20_000).Count;
    sink += Work.Checksum(50_000);
}
Console.WriteLine(sink);
