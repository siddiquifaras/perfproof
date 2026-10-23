using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Markdig;

// Workload for xoofx/markdig pull request 922 (PipeTableParser nested the
// inline tree one level per cell, so cell boundaries cost O(n^2) in the
// cells of a table): parse one pipe table of 600 rows and 4 columns to HTML.
// A fixed 3 warm-up and 5 timed iterations, then the median per
// iteration; it never fails on how it settles.
const int Rows = 600;
const int Warmup = 3;
const int Timed = 5;

var builder = new StringBuilder("| id | name | note | value |\n|---|---|---|---|\n");
for (var i = 0; i < Rows; i++)
{
    builder.Append(CultureInfo.InvariantCulture, $"| {i} | name {i} | `code {i}` and *text* | {i * 7 % 101} |\n");
}
var markdown = builder.ToString();
var pipeline = new MarkdownPipelineBuilder().UsePipeTables().Build();

string html = "";
for (var i = 0; i < Warmup; i++)
{
    html = Markdown.ToHtml(markdown, pipeline);
}
var times = new double[Timed];
for (var i = 0; i < Timed; i++)
{
    var start = Stopwatch.GetTimestamp();
    html = Markdown.ToHtml(markdown, pipeline);
    times[i] = Stopwatch.GetElapsedTime(start).TotalSeconds;
}
Array.Sort(times);
var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(html)));
Console.WriteLine($"html bytes {html.Length} sha256 {digest}");
Console.WriteLine($"PERFPROOF {{\"value\": {times[Timed / 2].ToString("R", CultureInfo.InvariantCulture)}}}");
