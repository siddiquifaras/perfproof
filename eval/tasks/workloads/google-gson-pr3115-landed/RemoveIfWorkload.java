import java.util.Arrays;

import com.google.gson.JsonArray;
import com.google.gson.JsonPrimitive;

/**
 * Remove two thirds of a 20,000 element JsonArray through its
 * {@code asList().removeIf(...)} view.
 *
 * <p>Iterates in process until the iteration time stops drifting (the
 * median of the last 10 iterations within 1% of the median of the 10
 * before, at least 200), then times 20 more and reports their median per
 * element examined as {@code PERFPROOF {"value": ns}}. If it has not
 * stabilised in 600 iterations it reports the median of the last 20
 * and says so on stderr, and still exits 0. The iteration
 * times go to stderr; stdout carries the checksum of one iteration,
 * which the oracle compares and which does not depend on how many
 * iterations ran.
 */
public final class RemoveIfWorkload {
    private static final int ELEMENTS = 20_000;
    private static final int REPS = 10;
    private static final int OPS_PER_ITERATION = ELEMENTS * REPS;
    private static final JsonPrimitive[] SOURCE = new JsonPrimitive[ELEMENTS];
    private static long sink;

    private static void setup() {
        for (int i = 0; i < ELEMENTS; i++) {
            SOURCE[i] = new JsonPrimitive(i);
        }
    }

    private static void iteration() {
        for (int rep = 0; rep < REPS; rep++) {
            JsonArray array = new JsonArray(ELEMENTS);
            for (JsonPrimitive element : SOURCE) {
                array.add(element);
            }
            array.asList().removeIf(e -> e.getAsInt() % 3 != 0);
            sink += array.size() + array.get(array.size() - 1).getAsInt();
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
