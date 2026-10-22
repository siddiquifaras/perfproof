/* Compress many small blocks with a digested dictionary at level 1,
 * attached by reference (a dictionary match state): the path that
 * https://github.com/facebook/zstd/pull/3152 speeds up with a "short
 * cache", run on a tree where that change is already in. Fixed work, no
 * timer; the data is generated from a fixed seed.
 *
 *   make -C lib libzstd.a
 *   cc -O2 -Ilib -o eval_cdict eval_cdict.c lib/libzstd.a
 */
#define ZSTD_STATIC_LINKING_ONLY
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "zstd.h"

enum { DICT_BYTES = 1 << 16, BLOCK = 4096, BLOCKS = 64, ROUNDS = 2500 };

static unsigned long long state = 88172645463325252ULL;

static unsigned next(void) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return (unsigned)(state >> 11);
}

/* Words from a fixed vocabulary, so blocks share matches with the
 * dictionary the way real small records do. */
static void fill(char *out, size_t n) {
    static const char *words[64];
    static char pool[64][12];
    static int ready;
    if (!ready) {
        for (int w = 0; w < 64; w++) {
            int len = 3 + (int)(next() % 8);
            for (int c = 0; c < len; c++) {
                pool[w][c] = (char)('a' + next() % 26);
            }
            pool[w][len] = '\0';
            words[w] = pool[w];
        }
        ready = 1;
    }
    size_t at = 0;
    while (at < n) {
        const char *word = words[next() % 64];
        size_t len = strlen(word);
        for (size_t i = 0; i < len && at < n; i++) {
            out[at++] = word[i];
        }
        if (at < n) {
            out[at++] = ' ';
        }
    }
}

int main(void) {
    char *dict = malloc(DICT_BYTES);
    char *data = malloc((size_t)BLOCK * BLOCKS);
    fill(dict, DICT_BYTES);
    fill(data, (size_t)BLOCK * BLOCKS);
    ZSTD_CDict *cdict = ZSTD_createCDict(dict, DICT_BYTES, 1);
    ZSTD_CCtx *cctx = ZSTD_createCCtx();
    if (cdict == NULL || cctx == NULL) {
        return 2;
    }
    ZSTD_CCtx_setParameter(cctx, ZSTD_c_forceAttachDict, ZSTD_dictForceAttach);
    size_t cap = ZSTD_compressBound(BLOCK);
    char *out = malloc(cap);
    unsigned long long total = 0;
    for (int r = 0; r < ROUNDS; r++) {
        for (int b = 0; b < BLOCKS; b++) {
            size_t got = ZSTD_compress_usingCDict(
                cctx, out, cap, data + (size_t)b * BLOCK, BLOCK, cdict);
            if (ZSTD_isError(got)) {
                fprintf(stderr, "%s\n", ZSTD_getErrorName(got));
                return 1;
            }
            total += got;
        }
    }
    printf("%d blocks of %d bytes -> %llu bytes\n", ROUNDS * BLOCKS, BLOCK,
           total);
    return 0;
}
