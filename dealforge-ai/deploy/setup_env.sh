#!/bin/bash

# DealForge AI — Environment Setup Wizard
# Helps bootstrap .env.docker for containerized deployment

ENV_FILE=".env.docker"

echo "════════════════════════════════════════════════════════════"
echo "   DealForge AI — Environment Setup Wizard"
echo "════════════════════════════════════════════════════════════"

if [ -f "$ENV_FILE" ]; then
    read -p ".env.docker already exists. Overwrite? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborting setup."
        exit 1
    fi
fi

echo "# DealForge AI — Docker Environment Configuration" > "$ENV_FILE"
echo "DEBUG=false" >> "$ENV_FILE"
echo "DEFAULT_LLM_PROVIDER=gemini" >> "$ENV_FILE"
echo "PAGEINDEX_MODE=local" >> "$ENV_FILE"

read -p "Enter GEMINI_API_KEY: " gemini_key
echo "GEMINI_API_KEY=$gemini_key" >> "$ENV_FILE"

read -p "Enter MISTRAL_API_KEY (optional): " mistral_key
echo "MISTRAL_API_KEY=$mistral_key" >> "$ENV_FILE"

read -p "Enter OPENAI_API_KEY (optional): " openai_key
echo "OPENAI_API_KEY=$openai_key" >> "$ENV_FILE"

echo "Adding default financial data overrides..."
echo "GEMINI_MODEL=gemini-2.0-flash" >> "$ENV_FILE"
echo "MISTRAL_MODEL=mistral-large-latest" >> "$ENV_FILE"
echo "OPENAI_MODEL=gpt-4o" >> "$ENV_FILE"

echo "════════════════════════════════════════════════════════════"
echo "  SUCCESS: .env.docker generated."
echo "  Review and add more keys (SEC, FMP, etc.) if needed."
echo "════════════════════════════════════════════════════════════"
