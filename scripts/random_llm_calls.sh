#!/bin/bash

# Check if environment variables are set
if [ -z "$DEV" ] || [ -z "$ADMIN" ]; then
    echo "Error: DEV and ADMIN environment variables must be set"
    exit 1
fi

# Array of random prompts
prompts=(
    "Write a haiku about artificial intelligence"
    "Explain quantum computing in three sentences"
    "Create a short story about a robot learning to paint"
    "Describe the future of space exploration"
    "Write a poem about the ocean"
    "Explain the concept of blockchain to a 5-year-old"
    "Create a dialogue between two AI systems discussing consciousness"
    "Write a limerick about machine learning"
    "Describe a day in the life of a sentient computer"
    "Write a short paragraph about the ethics of AI"
)

# Function to get random prompt
get_random_prompt() {
    local array=("$@")
    local size=${#array[@]}
    local index=$((RANDOM % size))
    echo "${array[$index]}"
}

# Function to get random delay between 1 and 30 seconds
get_random_delay() {
    echo $((RANDOM % 30 + 1))
}

# Function to make API call
make_api_call() {
    local auth_token=$1
    local prompt=$2
    local metadata_id=$3

    # Determine user based on auth token
    local user=""
    if [ "$auth_token" = "$ADMIN" ]; then
        user="ADMIN"
    else
        user="DEV"
    fi

    curl -X POST http://localhost:4000/chat/completions \
        -H "Authorization: Bearer $auth_token" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"ollama/llama3.2\", \"messages\": [{\"role\":\"user\", \
            \"content\":\"$prompt\"}], \"user\": \"$user\", \"metadata\":{\"ID\":\"$metadata_id\"}}"
}

# Make 5 calls alternating between DEV and ADMIN tokens
for i in {1..5}; do
    # Alternate between DEV and ADMIN tokens
    if [ $((i % 2)) -eq 0 ]; then
        auth_token=$ADMIN
        metadata_id="admin$i"
    else
        auth_token=$DEV
        metadata_id="dev$i"
    fi

    # Get random prompt
    prompt=$(get_random_prompt "${prompts[@]}")

    echo "Making call $i with prompt: $prompt"
    make_api_call "$auth_token" "$prompt" "$metadata_id"

    # Add random delay between calls (except after the last one)
    if [ $i -lt 5 ]; then
        delay=$(get_random_delay)
        echo "Waiting for $delay seconds..."
        sleep $delay
    fi
done

echo "All calls completed!"
