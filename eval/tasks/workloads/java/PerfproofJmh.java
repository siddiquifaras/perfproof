import java.util.Collection;

import org.openjdk.jmh.results.RunResult;
import org.openjdk.jmh.runner.Runner;
import org.openjdk.jmh.runner.options.Options;
import org.openjdk.jmh.runner.options.OptionsBuilder;
import org.openjdk.jmh.runner.options.TimeValue;

/**
 * Run one JMH benchmark and print its score as a perfproof reported value.
 *
 * <p>Usage: {@code PerfproofJmh <regex> [key=value ...]} where a key is a
 * benchmark parameter ({@code length=1000}). Exactly one benchmark with one
 * parameter set must match: the reported value is one number, and a regex
 * that matches several would hide which was meant. The score is in the
 * benchmark's own mode and unit (average time in ns/op is lower-is-better,
 * throughput in ops/s is higher-is-better); declare the spec's metric to
 * match. JMH does the warm-up, in forked JVMs. System properties
 * {@code jmh.forks}, {@code jmh.warmup}, {@code jmh.measure} and
 * {@code jmh.time} (seconds per iteration) override the defaults here
 * (one fork, 5 + 5 iterations of 2 s), which are far lighter than JMH's own.
 */
public final class PerfproofJmh {
    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("usage: PerfproofJmh <regex> [key=value ...]");
            System.exit(2);
        }
        OptionsBuilder builder = new OptionsBuilder();
        builder.include(args[0])
                .forks(Integer.getInteger("jmh.forks", 1))
                .warmupIterations(Integer.getInteger("jmh.warmup", 5))
                .warmupTime(TimeValue.seconds(Long.getLong("jmh.time", 2)))
                .measurementIterations(Integer.getInteger("jmh.measure", 5))
                .measurementTime(TimeValue.seconds(Long.getLong("jmh.time", 2)));
        for (int i = 1; i < args.length; i++) {
            int eq = args[i].indexOf('=');
            if (eq < 0) {
                System.err.println("expected key=value, got " + args[i]);
                System.exit(2);
            }
            builder.param(args[i].substring(0, eq), args[i].substring(eq + 1));
        }
        Options options = builder.build();
        Collection<RunResult> results = new Runner(options).run();
        if (results.size() != 1) {
            System.err.println("expected exactly one benchmark result, got "
                    + results.size() + "; narrow the regex or add key=value");
            System.exit(3);
        }
        RunResult result = results.iterator().next();
        double score = result.getPrimaryResult().getScore();
        String unit = result.getPrimaryResult().getScoreUnit();
        System.err.println("score " + score + " " + unit);
        System.out.println("PERFPROOF {\"value\": " + score + "}");
    }
}
