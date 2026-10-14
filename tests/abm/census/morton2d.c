#include <stdint.h>
static uint32_t part1by1(uint32_t x) {
  x &= 0x0000ffff;
  x = (x ^ (x << 8)) & 0x00ff00ff;
  x = (x ^ (x << 4)) & 0x0f0f0f0f;
  x = (x ^ (x << 2)) & 0x33333333;
  x = (x ^ (x << 1)) & 0x55555555;
  return x;
}
uint32_t morton2d(uint32_t x, uint32_t y) { return part1by1(x) | (part1by1(y) << 1); }
uint32_t crc_step(uint32_t c) { return (c >> 1) ^ (-(c & 1) & 0xEDB88320u); }
uint64_t xorshift128p(uint64_t s1, uint64_t s0) { s1 ^= s1 << 23; return s1 ^ s0 ^ (s1 >> 17) ^ (s0 >> 26); }
uint32_t merge(uint32_t a, uint32_t b) { return (a & 0xF0F0F0F0u) | (b & 0x0F0F0F0Fu); }
uint32_t bcast(uint32_t a) { return -((a >> 7) & 1); }
