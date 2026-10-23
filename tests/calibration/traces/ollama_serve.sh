#!/bin/bash
# Run Ollama for the LLM trace target with everything under one
# directory: ollama_serve.sh BINARY MODELS_DIR. One request at a time,
# one model loaded, on port 11500 so it never meets another Ollama.
export OLLAMA_HOST=127.0.0.1:11500
export OLLAMA_MODELS=$2
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
exec "$1" serve
