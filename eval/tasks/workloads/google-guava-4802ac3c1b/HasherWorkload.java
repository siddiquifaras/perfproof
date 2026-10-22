import java.util.Arrays;

import com.google.common.hash.HashFunction;
import com.google.common.hash.Hashing;

/**
 * Hash a few primitives, a string and a byte array through a streaming
 * {@code Hasher} of a non-streaming hash function (FarmHash fingerprint),
 * the path Guava's {@code AbstractNonStreamingHashFunction} buffers.
 *
 * <p>Iterates in process until the iteration time stops drifting (the
 * median of the last 10 iterations within 1% of the median of the 10
 * before, at least 200), then times 20 more and reports their median per
 * hash as {@code PERFPROOF {"value": ns}}. If it has not
 * stabilised in 600 iterations it reports the median of the last 20
 * and says so on stderr, and still exits 0. The iteration times go to
 * stderr; stdout carries the checksum of one iteration, which the oracle
 * compares and which does not depend on how many iterations ran.
 */
public final class HasherWorkload {
    private static final HashFunction FUNCTION = Hashing.farmHashFingerprint64();
    private static final int OPS_PER_ITERATION = 200_000;
    private static final String TEXT = "the quick brown fox jumps over the lazy dog";
    private static final byte[] BYTES = new byte[200];
    private static long sink;

    private static void setup() {
        for (int i = 0; i < BYTES.length; i++) {
            BYTES[i] = (byte) (i * 31);
        }
    }

    private static void iteration() {
        for (int i = 0; i < OPS_PER_ITERATION; i++) {
            sink += FUNCTION.newHasher()
                    .putInt(i)
                    .putLong(i * 2654435761L)
                    .putBoolean((i & 1) == 0)
                    .putUnencodedChars(TEXT)
                    .putBytes(BYTES)
                    .hash()
                    .asLong();
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
