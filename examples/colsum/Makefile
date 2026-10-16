# Column sums of a row-major matrix: kernel.c walks it column by column,
# candidates/fix/kernel.c walks it row by row.
#
#   make          build $(BUILD)/colsum from this tree's kernel.c
#   make bench    run it once; prints PERFPROOF {"value": <seconds>}
#   make check    build the baseline and the fix, run both on the same
#                 matrix and fail unless their sums are byte-identical

CC ?= cc
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Wpedantic -Werror
BUILD ?= build
ROWS ?= 4096
COLS ?= 4096

.PHONY: all bench check clean

all: $(BUILD)/colsum

$(BUILD):
	mkdir -p $@

$(BUILD)/colsum: bench.c kernel.c kernel.h | $(BUILD)
	$(CC) $(CFLAGS) -I. -o $@ bench.c kernel.c

$(BUILD)/colsum-fix: bench.c candidates/fix/kernel.c kernel.h | $(BUILD)
	$(CC) $(CFLAGS) -I. -o $@ bench.c candidates/fix/kernel.c

bench: $(BUILD)/colsum
	$(BUILD)/colsum $(ROWS) $(COLS) $(BUILD)/sums.txt

check: $(BUILD)/colsum $(BUILD)/colsum-fix
	$(BUILD)/colsum $(ROWS) $(COLS) $(BUILD)/sums-baseline.txt
	$(BUILD)/colsum-fix $(ROWS) $(COLS) $(BUILD)/sums-fix.txt
	cmp $(BUILD)/sums-baseline.txt $(BUILD)/sums-fix.txt

clean:
	rm -rf $(BUILD)
