# Claude AI Setup Guide

This guide will help you set up your Telegram bot to use Claude AI instead of OpenAI GPT or Google Gemini.

## Prerequisites

1. A Telegram bot token (get it from [@BotFather](https://t.me/BotFather))
2. A Claude API key from [Anthropic](https://console.anthropic.com/)

## Installation

1. **Install the required Python package**:
   ```bash
   pip install anthropic>=0.40.0
   ```

2. **Configure your environment**:
   Copy the example environment file for Claude:
   ```bash
   cp .env.claude.example .env
   ```

3. **Edit the `.env` file** with your actual credentials:
   ```env
   AI_PROVIDER=claude
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   CLAUDE_API_KEY=your_claude_api_key_here
   ```

## Configuration Options

### Claude-specific Settings

- `AI_PROVIDER=claude` - Use Claude as the AI provider
- `CLAUDE_API_KEY` - Your Claude API key from Anthropic
- `CLAUDE_MODEL` - Model to use (default: `claude-3-5-sonnet-20241022`)
- `CLAUDE_MAX_TOKENS` - Maximum tokens for Claude responses (default: `4096`)

### Supported Features

✅ **Supported by Claude**:
- Text chat (streaming and non-streaming)
- Vision (image analysis)
- Conversation history
- Multi-user chat support

❌ **Not supported by Claude** (will show error messages):
- Image generation
- Text-to-speech (TTS)
- Audio transcription

### Available Claude Models

- `claude-3-5-sonnet-20241022` (recommended) - Latest and most capable
- `claude-3-5-haiku-20241022` - Faster and cheaper
- `claude-3-opus-20240229` - Most capable for complex tasks

## Usage

1. **Start the bot**:
   ```bash
   python bot/main.py
   ```

2. **Test the bot** by sending a message to your Telegram bot

3. **Test vision** by sending an image with a caption

## Common Settings

```env
# Enable streaming responses (recommended)
STREAM=true

# Set conversation memory
MAX_HISTORY_SIZE=15
MAX_CONVERSATION_AGE_MINUTES=180

# Customize the assistant personality
ASSISTANT_PROMPT=You are a helpful assistant.

# Enable vision for image analysis
ENABLE_VISION=true

# Disable unsupported features
ENABLE_IMAGE_GENERATION=false
ENABLE_TTS_GENERATION=false
ENABLE_TRANSCRIPTION=false
```

## Troubleshooting

1. **"Import anthropic could not be resolved"**:
   ```bash
   pip install anthropic>=0.40.0
   ```

2. **"Invalid API key"**:
   - Make sure your `CLAUDE_API_KEY` is correct
   - Check that your API key has proper permissions

3. **"Model not found"**:
   - Verify the model name in `CLAUDE_MODEL`
   - Check if you have access to the specified model

4. **Rate limiting**:
   - Claude has rate limits; the bot will automatically retry with exponential backoff
   - Consider using `claude-3-5-haiku-20241022` for higher throughput

## Cost Considerations

Claude pricing (as of 2024):
- Claude 3.5 Sonnet: $3.00/million input tokens, $15.00/million output tokens
- Claude 3.5 Haiku: $0.25/million input tokens, $1.25/million output tokens

Set appropriate budgets in your `.env` file:
```env
USER_BUDGETS=*  # or specific amounts like "user1:10.0,user2:20.0"
GUEST_BUDGET=100.0
```
