# The Linux image the .NET integration tests run in: the official SDK
# image, dotnet-trace, and uv to run perfproof's own tests.
#
#   docker build -t perfproof-dotnet-it tests/fixtures/dotnet
#   docker run --rm -v "$PWD":/w -w /w -e UV_PROJECT_ENVIRONMENT=/venv \
#     perfproof-dotnet-it uv run pytest -m integration \
#     tests/profilers/test_dotnet.py tests/bench/test_lock_dotnet.py
FROM mcr.microsoft.com/dotnet/sdk:10.0
COPY --from=ghcr.io/astral-sh/uv:bookworm-slim /usr/local/bin/uv /usr/local/bin/uv
RUN dotnet tool install --global dotnet-trace
ENV PATH="${PATH}:/root/.dotnet/tools"
