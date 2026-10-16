# The official Ruby 4.0 image plus Debian's tzdata-legacy. Debian trixie
# moved the legacy zone names (US/Eastern and the like), which Active
# Support's tests use, out of tzdata into this package.
FROM ruby:4.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata-legacy \
    && rm -rf /var/lib/apt/lists/*
