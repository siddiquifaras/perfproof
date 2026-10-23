# The Linux image the Ruby integration tests run in: the official Ruby
# image, the stackprof gem, and uv to run perfproof's own tests.
#
#   docker build -t perfproof-ruby-it tests/fixtures/ruby
#   docker run --rm -v "$PWD":/w -w /w -e UV_PROJECT_ENVIRONMENT=/venv \
#     perfproof-ruby-it uv run pytest -m integration \
#     tests/profilers/test_ruby.py tests/bench/test_lock_ruby.py
FROM ruby:4.0
COPY --from=ghcr.io/astral-sh/uv:bookworm-slim /usr/local/bin/uv /usr/local/bin/uv
RUN gem install --no-document stackprof simplecov simplecov-lcov
