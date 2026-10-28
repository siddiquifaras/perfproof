// CUDA C++ workload: a shared-memory tiled fp32 matrix multiply.
//
// 4096 x 4096 x 4096, 16 x 16 tiles, written the textbook way on
// purpose: the kernel is plain CUDA C++ with no library, launched
// from a native binary, so it adds the "CUDA kernels" half of the
// class that the PyTorch workloads do not. Build in each tree:
//   nvcc -O3 -std=c++17 -arch=native -o sgemm sgemm.cu
// Prints PERFPROOF {"value": <GPU seconds for ITERS launches>,
// "iters": ITERS,
// "peak_gpu_mem_bytes": <bytes allocated on the device>} and checks two
// corner elements against a host dot product (exit 1 on a mismatch).
#include <cmath>
#include <cstdio>
#include <vector>

#include <cuda_runtime.h>

constexpr int N = 4096;
constexpr int TILE = 16;
constexpr int WARMUP = 2;
constexpr int ITERS = 10;

#define CHECK(call)                                                      \
  do {                                                                   \
    cudaError_t err_ = (call);                                           \
    if (err_ != cudaSuccess) {                                           \
      std::fprintf(stderr, "CUDA error %s at %s:%d\n",                   \
                   cudaGetErrorString(err_), __FILE__, __LINE__);        \
      return 1;                                                          \
    }                                                                    \
  } while (0)

__global__ void sgemm(const float* a, const float* b, float* c) {
  __shared__ float sa[TILE][TILE];
  __shared__ float sb[TILE][TILE];
  const int row = blockIdx.y * TILE + threadIdx.y;
  const int col = blockIdx.x * TILE + threadIdx.x;
  float acc = 0.0f;
  for (int t = 0; t < N; t += TILE) {
    sa[threadIdx.y][threadIdx.x] = a[row * N + t + threadIdx.x];
    sb[threadIdx.y][threadIdx.x] = b[(t + threadIdx.y) * N + col];
    __syncthreads();
    for (int k = 0; k < TILE; ++k) {
      acc += sa[threadIdx.y][k] * sb[k][threadIdx.x];
    }
    __syncthreads();
  }
  c[row * N + col] = acc;
}

static float a_at(int r, int c) { return ((r * 31 + c * 17) % 23 - 11) / 16.0f; }
static float b_at(int r, int c) { return ((r * 13 + c * 7) % 19 - 9) / 16.0f; }

int main() {
  const size_t count = static_cast<size_t>(N) * N;
  const size_t bytes = count * sizeof(float);
  std::vector<float> ha(count), hb(count), hc(count);
  for (int r = 0; r < N; ++r) {
    for (int c = 0; c < N; ++c) {
      ha[static_cast<size_t>(r) * N + c] = a_at(r, c);
      hb[static_cast<size_t>(r) * N + c] = b_at(r, c);
    }
  }
  float *da = nullptr, *db = nullptr, *dc = nullptr;
  CHECK(cudaMalloc(&da, bytes));
  CHECK(cudaMalloc(&db, bytes));
  CHECK(cudaMalloc(&dc, bytes));
  CHECK(cudaMemcpy(da, ha.data(), bytes, cudaMemcpyHostToDevice));
  CHECK(cudaMemcpy(db, hb.data(), bytes, cudaMemcpyHostToDevice));
  const dim3 block(TILE, TILE);
  const dim3 grid(N / TILE, N / TILE);
  for (int i = 0; i < WARMUP; ++i) sgemm<<<grid, block>>>(da, db, dc);
  CHECK(cudaGetLastError());
  CHECK(cudaDeviceSynchronize());
  cudaEvent_t start, stop;
  CHECK(cudaEventCreate(&start));
  CHECK(cudaEventCreate(&stop));
  CHECK(cudaEventRecord(start));
  for (int i = 0; i < ITERS; ++i) sgemm<<<grid, block>>>(da, db, dc);
  CHECK(cudaEventRecord(stop));
  CHECK(cudaEventSynchronize(stop));
  CHECK(cudaGetLastError());
  float ms = 0.0f;
  CHECK(cudaEventElapsedTime(&ms, start, stop));
  CHECK(cudaMemcpy(hc.data(), dc, bytes, cudaMemcpyDeviceToHost));
  const int corners[2][2] = {{0, 0}, {N - 1, N - 1}};
  for (const auto& rc : corners) {
    double want = 0.0;
    for (int k = 0; k < N; ++k) {
      want += static_cast<double>(a_at(rc[0], k)) * b_at(k, rc[1]);
    }
    const double got = hc[static_cast<size_t>(rc[0]) * N + rc[1]];
    if (std::fabs(got - want) > 1e-2) {
      std::fprintf(stderr, "wrong result at (%d,%d): got %f want %f\n",
                   rc[0], rc[1], got, want);
      return 1;
    }
  }
  std::printf(
      "PERFPROOF {\"value\": %.9f, \"iters\": %d, "
      "\"peak_gpu_mem_bytes\": %zu}\n",
      ms / 1000.0, ITERS, 3 * bytes);
  cudaFree(da);
  cudaFree(db);
  cudaFree(dc);
  return 0;
}
