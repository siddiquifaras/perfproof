package com.example;

public final class Calc {
    private Calc() {}

    public static int add(int a, int b) {
        int total = a + b;
        return total;
    }

    public static int unused(int x) {
        return x * 2;
    }
}
