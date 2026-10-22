import java.io.ByteArrayOutputStream;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.Arrays;
import java.util.Random;

import tools.jackson.core.JsonGenerator;
import tools.jackson.core.ObjectWriteContext;
import tools.jackson.core.StreamWriteFeature;
import tools.jackson.core.json.JsonFactory;

/**
 * Write BigDecimal values of about 30 digits as JSON numbers through
 * Jackson's UTF-8 generator, as a serializer does for decimal amounts.
 * Each value's text is built once (BigDecimal caches it), so the time is
 * the generator's copy of that text into its output buffer.
 *
 * <p>Iterates in process until the iteration time stops drifting (the
 * median of the last 10 iterations within 1% of the median of the 10
 * before, at least 200), then times 20 more and reports their median per
 * number written as {@code PERFPROOF {"value": ns}}. If it has not
 * stabilised in 600 iterations it reports the median of the last 20
 * and says so on stderr, and still exits 0. The iteration times
 * go to stderr; stdout carries the checksum of one iteration, which the oracle
 * compares and which does not depend on how many iterations ran.
 */
public final class NumberWriteWorkload {
    private static final int VALUES = 2000;
    private static final int REPS = 300;
    private static final int OPS_PER_ITERATION = VALUES * REPS;
    private static final JsonFactory FACTORY = JsonFactory.builder()
            .disable(StreamWriteFeature.USE_FAST_DOUBLE_WRITER)
            .build();

    private static final BigDecimal[] DECIMALS = new BigDecimal[VALUES];
    private static long sink;

    private static void setup() {
        Random random = new Random(42);
        for (int i = 0; i < VALUES; i++) {
            DECIMALS[i] = new BigDecimal(new BigInteger(100, random), 12);
            DECIMALS[i].toString();
        }
    }

    private static void iteration() {
        ByteArrayOutputStream out = new ByteArrayOutputStream(1 << 20);
        for (int rep = 0; rep < REPS; rep++) {
            out.reset();
            try (JsonGenerator g = FACTORY.createGenerator(ObjectWriteContext.empty(), out)) {
                g.writeStartArray();
                for (int i = 0; i < VALUES; i++) {
                    g.writeNumber(DECIMALS[i]);
                }
                g.writeEndArray();
            }
            sink += out.size();
        }
    }

    private static double median(long[] v, int from, int to) {
        long[] w = Arrays.copyOfRange(v, from, to);
        Arrays.sort(w);
        return w[w.length / 2];
    }

    public static void main(String[] args) {
        setup();
        iteration();
        final long checksum = sink;
        final int block = 10;
        final double tolerance = 0.01;
        final int minWarmup = 200;
        final int maxWarmup = 600;
        final int measured = 20;
        long[] times = new long[maxWarmup];
        int n = 0;
        boolean stable = false;
        while (n < maxWarmup && !stable) {
            long t0 = System.nanoTime();
            iteration();
            times[n++] = System.nanoTime() - t0;
            System.err.println("warmup " + n + " " + times[n - 1] / 1000 + " us");
            if (n >= minWarmup) {
                double last = median(times, n - block, n);
                double before = median(times, n - 2 * block, n - block);
                stable = Math.abs(last - before) < tolerance * last;
            }
        }
        long[] m = new long[measured];
        if (stable) {
            for (int i = 0; i < measured; i++) {
                long t0 = System.nanoTime();
                iteration();
                m[i] = System.nanoTime() - t0;
            }
            System.err.println("stabilised after " + n + " iterations");
        } else {
            System.arraycopy(times, n - measured, m, 0, measured);
            System.err.println("did not stabilise after " + n
                    + " iterations; reporting the median of the last " + measured);
        }
        Arrays.sort(m);
        System.out.println(checksum);
        System.out.println(
                "PERFPROOF {\"value\": " + (double) m[measured / 2] / OPS_PER_ITERATION + "}");
    }
}
