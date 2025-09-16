# Google AI Setup Instructions

## Overview
This bot now supports both OpenAI and Google AI (Gemini) as AI providers. You can switch between them by changing the `AI_PROVIDER` environment variable.

## Setup for Google AI

### 1. Install Dependencies
```bash
pip install google-genai~=0.1.0
```

### 2. Get Google AI API Key
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new API key
3. Copy the API key

### 3. Configure Environment Variables

Add these variables to your `.env` file:

```env
# AI Provider (set to 'google' for Google AI)
AI_PROVIDER=google

# Google AI Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GOOGLE_MODEL=gemini-2.0-flash-exp
GOOGLE_VISION_MODEL=gemini-2.0-flash-exp

# Other required variables
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
BOT_LANGUAGE=en
STREAM=true
TEMPERATURE=1.0
TOP_P=1.0
MAX_OUTPUT_TOKENS=8192
ASSISTANT_PROMPT=You are a helpful assistant.

# User Management
ALLOWED_TELEGRAM_USER_IDS=*
ADMIN_USER_IDS=-

# Budget Configuration
BUDGET_PERIOD=monthly
USER_BUDGETS=*
GUEST_BUDGET=100.0

# Features (note: some features are not supported with Google AI)
ENABLE_IMAGE_GENERATION=false
ENABLE_TRANSCRIPTION=false
ENABLE_VISION=true
ENABLE_TTS_GENERATION=false
ENABLE_WEB_SEARCH=false

# Group Chat Settings
GROUP_TRIGGER_KEYWORD=
GROUP_IGNORE_KEYWORD=
IGNORE_GROUP_VISION=true

# Plugins
PLUGINS=

# Vision Settings
VISION_PROMPT=What is in this image?

# Logging
SHOW_PLUGINS_USED=false
```

### 4. Run the Bot
```bash
python bot/main.py
```

## Supported Features with Google AI

### ✅ Supported
- **Chat**: Full conversation support with Gemini Pro
- **Vision**: Image analysis with Gemini Pro Vision
- **Streaming**: Real-time response streaming
- **Event Logging**: All events are logged for debugging

### ❌ Not Supported
- **Image Generation**: Google AI doesn't have a dedicated image generation API like DALL-E
- **Text-to-Speech**: No built-in TTS service
- **Audio Transcription**: No built-in transcription service
- **Function Calling**: No built-in function calling like OpenAI

## Switching Between Providers

To switch back to OpenAI, change your `.env` file:

```env
AI_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o
# ... other OpenAI specific settings
```

## Models Available

### Google AI Models
- `gemini-2.0-flash-exp`: Latest general purpose model
- `gemini-2.5-flash`: Fast and efficient model
- `gemini-1.5-pro`: High-quality model for complex tasks

### OpenAI Models (when using OpenAI provider)
- `gpt-4o`: Latest GPT-4 model
- `gpt-4o-mini`: Faster, cheaper alternative
- `gpt-3.5-turbo`: Legacy model

## Troubleshooting

### Common Issues

1. **API Key Error**: Make sure your Google AI API key is valid and has the necessary permissions
2. **Model Not Found**: Ensure you're using a valid model name
3. **Vision Not Working**: Make sure you're using `gemini-pro-vision` for vision tasks
4. **Streaming Issues**: Some models might not support streaming properly

### Logs
Check the `event_logs.log` file for detailed event logging and debugging information.

## Performance Notes

- Google AI responses might be slightly different from OpenAI
- Vision processing is generally faster with Google AI
- Streaming works well with both providers
- Function calling is not available with Google AI, so plugins that rely on it won't work
