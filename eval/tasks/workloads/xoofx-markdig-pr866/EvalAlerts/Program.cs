using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Markdig;

// Workload for xoofx/markdig pull request 866 (the alert block parser's
// fast path and its allocations): parse a document of many links, which
// the alert inline parser looks at, and a few alert blocks, to HTML.
// A fixed 5 warm-up and 7 timed iterations of 200 parses each, then the
// median per parse; it never fails on how it settles.
const int Paragraphs = 120;
const int ParsesPerIteration = 200;
const int Warmup = 5;
const int Timed = 7;

var builder = new StringBuilder();
for (var i = 0; i < Paragraphs; i++)
{
    builder.Append(CultureInfo.InvariantCulture,
        $"Paragraph {i} has [a link](https://example.com/{i}) and [another](/docs/{i}) and a [ref{i}][r].\n\n");
    if (i % 12 == 0)
    {
        builder.Append(CultureInfo.InvariantCulture,
            $"> [!NOTE]\n> Alert {i} with a [link](https://example.com/n{i}).\n\n");
    }
}
builder.Append("[r]: https://example.com/ref\n");
var markdown = builder.ToString();
var pipeline = new MarkdownPipelineBuilder().UseAlertBlocks().Build();

string html = "";
for (var i = 0; i < Warmup; i++)
{
    for (var j = 0; j < ParsesPerIteration; j++)
    {
        html = Markdown.ToHtml(markdown, pipeline);
    }
}
var times = new double[Timed];
for (var i = 0; i < Timed; i++)
{
    var start = Stopwatch.GetTimestamp();
    for (var j = 0; j < ParsesPerIteration; j++)
    {
        html = Markdown.ToHtml(markdown, pipeline);
    }
    times[i] = Stopwatch.GetElapsedTime(start).TotalSeconds / ParsesPerIteration;
}
Array.Sort(times);
var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(html)));
Console.WriteLine($"html bytes {html.Length} sha256 {digest}");
Console.WriteLine($"PERFPROOF {{\"value\": {times[Timed / 2].ToString("R", CultureInfo.InvariantCulture)}}}");
