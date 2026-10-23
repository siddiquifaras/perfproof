import java.util.Arrays;

/**
 * Deterministic CPU-bound target for the real noise traces: generate a
 * fixed key set from an integer LCG, sort it and print a checksum of
 * the order.
 */
public final class Bench {
    private static final int N = 500_000;

    private Bench() {
    }

    public static void main(String[] args) {
        long state = 12345;
        long[] keys = new long[N];
        for (int i = 0; i < N; i++) {
            state = (state * 1103515245L + 12345L) & 0xffffffffL;
            long group = state % 997;
            state = (state * 1103515245L + 12345L) & 0xffffffffL;
            long score = state % 1000003;
            keys[i] = (group << 40) | (score << 20) | (i & 0xfffff);
        }
        Arrays.sort(keys);
        long sum = 0;
        for (int i = 0; i < N; i++) {
            sum = (sum * 31 + (keys[i] & 0xfffff)) & 0xffffffffL;
        }
        System.out.println("checksum " + sum);
    }
}
