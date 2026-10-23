/* Four worker threads, each running process_entry for a fixed time. */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

#define WORKERS 4

static volatile uint64_t sink;

__attribute__((noinline)) static uint64_t checksum(uint64_t seed, int rounds) {
    uint64_t h = seed;
    for (int i = 0; i < rounds; i++) {
        h = h * 6364136223846793005ULL + 1442695040888963407ULL;
        h ^= h >> 29;
    }
    return h;
}

__attribute__((noinline)) static void process_entry(uint64_t id) {
    sink += checksum(id, 4000);
}

static void *worker(void *arg) {
    uint64_t id = (uint64_t)(uintptr_t)arg;
    time_t end = time(NULL) + 6;
    while (time(NULL) < end) {
        process_entry(id);
    }
    return NULL;
}

int main(void) {
    pthread_t threads[WORKERS];
    for (int i = 0; i < WORKERS; i++) {
        pthread_create(&threads[i], NULL, worker, (void *)(uintptr_t)i);
    }
    for (int i = 0; i < WORKERS; i++) {
        pthread_join(threads[i], NULL);
    }
    printf("%llu\n", (unsigned long long)sink);
    return 0;
}
