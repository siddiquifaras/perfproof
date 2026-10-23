namespace Prog;

// A small library with known hot spots for the profiler and coverage
// fixtures: quadratic string building, allocation-heavy dictionaries,
// and a method no test reaches.
public static class Work
{
    public static string JoinSlow(int count)
    {
        var text = "";
        for (var i = 0; i < count; i++)
        {
            text = text + i.ToString() + ",";
        }
        return text;
    }

    public static Dictionary<string, int> Histogram(int count)
    {
        var counts = new Dictionary<string, int>();
        for (var i = 0; i < count; i++)
        {
            var key = "k" + (i % 97).ToString();
            counts[key] = counts.GetValueOrDefault(key) + 1;
        }
        return counts;
    }

    public static long Checksum(int count)
    {
        long total = 0;
        for (var i = 0; i < count; i++)
        {
            total = (total * 31 + i) % 1_000_003;
        }
        return total;
    }

    public static string NeverCalled()
    {
        return "unreached";
    }
}
