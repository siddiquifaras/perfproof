import java.lang.reflect.Method;
import java.util.*;
import java.util.stream.*;

/**
 * One hot loop per Java slowness class: the program the cheap tests in
 * skills/perfproof/java.md were checked with (run as {@code java Pathologies
 * <class>}, under Flight Recorder or async-profiler).
 */
public final class Pathologies {
    static long sink;
    static final Object LOCK = new Object();

    static void boxing() {
        Map<Integer, Long> m = new HashMap<>();
        for (int i = 0; i < 2_000_000; i++) {
            m.merge(i & 1023, (long) i, Long::sum);
        }
        sink += m.size();
    }

    static void strings() {
        String s = "";
        for (int i = 0; i < 4000; i++) {
            s += i;
        }
        for (int i = 0; i < 200_000; i++) {
            sink += String.format("%d-%s", i, "x").length();
        }
        sink += s.length();
    }

    static void collections() {
        for (int r = 0; r < 200; r++) {
            List<Integer> l = new ArrayList<>();
            Map<Integer, Integer> m = new HashMap<>();
            for (int i = 0; i < 20_000; i++) {
                l.add(i);
                m.put(i, i);
            }
            sink += l.size() + m.size();
        }
    }

    static void streams() {
        List<Integer> l = IntStream.range(0, 1000).boxed().collect(Collectors.toList());
        for (int r = 0; r < 20_000; r++) {
            sink += l.stream().filter(x -> (x & 1) == 0).mapToInt(x -> x * 2).sum();
        }
    }

    static void reflection() throws Exception {
        for (int r = 0; r < 300_000; r++) {
            Method m = Integer.class.getMethod("intValue");
            sink += (Integer) m.invoke(Integer.valueOf(r));
        }
    }

    static void exceptions() {
        for (int r = 0; r < 300_000; r++) {
            try {
                Integer.parseInt("x" + (r & 7));
            } catch (NumberFormatException e) {
                sink++;
            }
        }
    }

    static void locks() throws Exception {
        Thread[] ts = new Thread[4];
        for (int t = 0; t < ts.length; t++) {
            ts[t] = new Thread(() -> {
                for (int i = 0; i < 3_000_000; i++) {
                    synchronized (LOCK) {
                        sink++;
                    }
                }
            });
            ts[t].start();
        }
        for (Thread t : ts) {
            t.join();
        }
    }

    interface Shape { int area(); }
    record Sq(int s) implements Shape { public int area() { return s * s; } }
    record Rc(int a, int b) implements Shape { public int area() { return a * b; } }
    record Tr(int a) implements Shape { public int area() { return a; } }

    static long call(Shape sh) { return sh.area(); }

    static void deopt() {
        Shape[] mono = {new Sq(2)};
        for (int i = 0; i < 3_000_000; i++) {
            sink += call(mono[0]);
        }
        Shape[] poly = {new Sq(2), new Rc(2, 3), new Tr(4)};
        for (int i = 0; i < 3_000_000; i++) {
            sink += call(poly[i % 3]);
        }
    }

    public static void main(String[] args) throws Exception {
        String which = args.length > 0 ? args[0] : "all";
        for (int pass = 0; pass < 3; pass++) {
            if (which.equals("all") || which.equals("boxing")) boxing();
            if (which.equals("all") || which.equals("strings")) strings();
            if (which.equals("all") || which.equals("collections")) collections();
            if (which.equals("all") || which.equals("streams")) streams();
            if (which.equals("all") || which.equals("reflection")) reflection();
            if (which.equals("all") || which.equals("exceptions")) exceptions();
            if (which.equals("all") || which.equals("locks")) locks();
            if (which.equals("all") || which.equals("deopt")) deopt();
        }
        System.out.println(sink);
    }
}
