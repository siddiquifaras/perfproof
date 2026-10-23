using Prog;
using Xunit;

public class WorkTests
{
    [Fact]
    public void JoinSlow() => Assert.Equal("0,1,2,", Work.JoinSlow(3));

    [Fact]
    public void Histogram() => Assert.Equal(2, Work.Histogram(98)["k0"]);

    [Fact]
    public void Checksum() => Assert.Equal(1, Work.Checksum(2));
}
