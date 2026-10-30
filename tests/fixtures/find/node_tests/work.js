function checksum(n) {
  let total = 0;
  for (let i = 0; i < n; i++) {
    total += (i * i) % 7;
  }
  return total;
}

module.exports = { checksum };
