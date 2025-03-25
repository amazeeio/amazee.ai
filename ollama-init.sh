#!/bin/sh
# Start Ollama in the background
ollama serve &

# Wait for Ollama to start
sleep 5

# Pull Llama 3.2
ollama pull llama3.2

# Keep the container running by waiting for the Ollama process
wait