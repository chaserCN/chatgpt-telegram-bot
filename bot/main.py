import logging
import os
import json
import warnings
import argparse
import shutil

from dotenv import load_dotenv

# Filter out Pydantic field shadowing warnings
warnings.filterwarnings('ignore', message='Field name.*shadows an attribute')

from plugin_manager import PluginManager
from openai_helper2 import OpenAIHelper2
from googleai import GoogleAIHelper
from claude_helper import ClaudeHelper
from telegram_bot import ChatGPTTelegramBot


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Telegram Bot with AI support')
    parser.add_argument('-e', '--env', 
                       help='Path to .env file (default: .env in project root)',
                       default='.env')
    return parser.parse_args()


def cleanup_plugin_directories():
    """Clean up plugin download directories on startup"""
    try:
        # Get project root directory
        project_root = os.path.dirname(os.path.dirname(__file__))
        
        # Simply remove the entire uploads directory
        uploads_dir = os.path.join(project_root, 'uploads')
        if os.path.exists(uploads_dir):
            try:
                shutil.rmtree(uploads_dir)
                logging.info(f'Removed uploads directory: {uploads_dir}')
            except Exception as e:
                logging.warning(f'Failed to remove uploads directory: {e}')
        
    except Exception as e:
        logging.error(f'Error during plugin directories cleanup: {e}')


def main():
    # Parse command line arguments
    args = parse_arguments()
    
    # Read .env file
    env_file = args.env
    if not os.path.isabs(env_file):
        # If relative path, make it relative to project root (parent of bot directory)
        env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), env_file)
    
    # Setup logging first
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    logging.info(f'[MAIN] Loading .env file: {env_file}')
    load_dotenv(env_file)
    
    # Clean up plugin directories on startup
    cleanup_plugin_directories()

    # Check if the required environment variables are set
    ai_provider = os.environ.get('AI_PROVIDER', 'openai').lower()
    
    if ai_provider == 'openai':
        required_values = ['TELEGRAM_BOT_TOKEN', 'OPENAI_API_KEY']
    elif ai_provider == 'google':
        required_values = ['TELEGRAM_BOT_TOKEN', 'GEMINI_API_KEY']
    elif ai_provider == 'claude':
        required_values = ['TELEGRAM_BOT_TOKEN', 'CLAUDE_API_KEY']
    else:
        logging.error(f'Unsupported AI provider: {ai_provider}. Supported providers: openai, google, claude')
        exit(1)
    
    missing_values = [value for value in required_values if os.environ.get(value) is None]
    if len(missing_values) > 0:
        logging.error(f'The following environment values are missing in your .env: {", ".join(missing_values)}')
        exit(1)

    # Setup configurations
    if ai_provider == 'openai':
        model = os.environ.get('OPENAI_MODEL', 'gpt-4o')
    elif ai_provider == 'google':
        model = os.environ.get('GOOGLE_MODEL', 'gemini-3-pro-preview')
    else:  # claude
        model = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514')

    # Common configuration
    common_config = {
        'stream': os.environ.get('STREAM', 'true').lower() == 'true',
        'max_history_size': int(os.environ.get('MAX_HISTORY_SIZE', 15)),
        'max_conversation_age_minutes': int(os.environ.get('MAX_CONVERSATION_AGE_MINUTES', 180)),
        'assistant_prompt': os.environ.get('ASSISTANT_PROMPT', 'You are a helpful assistant.'),
        'temperature': float(os.environ.get('TEMPERATURE', 1.0)),
        'top_p': float(os.environ.get('TOP_P', 1.0)),
        'max_output_tokens': int(os.environ.get('MAX_OUTPUT_TOKENS', 8192)),
        'model': model,
        'bot_language': os.environ.get('BOT_LANGUAGE', 'en'),
        'show_plugins_used': os.environ.get('SHOW_PLUGINS_USED', 'false').lower() == 'true',
        'vision_prompt': os.environ.get('VISION_PROMPT', 'What is in this image'),
    }
    
    if ai_provider == 'openai':
        ai_config = {
            **common_config,
            'api_key': os.environ['OPENAI_API_KEY'],
            'image_model': os.environ.get('IMAGE_MODEL', 'dall-e-2'),
            'image_quality': os.environ.get('IMAGE_QUALITY', 'standard'),
            'image_style': os.environ.get('IMAGE_STYLE', 'vivid'),
            'image_size': os.environ.get('IMAGE_SIZE', '512x512'),
            'functions_max_consecutive_calls': int(os.environ.get('FUNCTIONS_MAX_CONSECUTIVE_CALLS', 10)),
            'presence_penalty': float(os.environ.get('PRESENCE_PENALTY', 0.0)),
            'frequency_penalty': float(os.environ.get('FREQUENCY_PENALTY', 0.0)),
            'whisper_prompt': os.environ.get('WHISPER_PROMPT', ''),
            'vision_detail': os.environ.get('VISION_DETAIL', 'auto'),
            'tts_model': os.environ.get('TTS_MODEL', 'tts-1'),
            'tts_voice': os.environ.get('TTS_VOICE', 'alloy'),
            'tts_speed': float(os.environ.get('TTS_SPEED', '0.7')),
            'tts_instructions': os.environ.get(
                'TTS_INSTRUCTIONS',
                'Speak in French. Speak slowly with very clear articulation and careful pronunciation. '
                'Pause slightly between phrases. The listener does not know French and needs to repeat the '
                'phrase clearly while in France.'
            ),
            'enable_web_search': os.environ.get('ENABLE_WEB_SEARCH', 'true').lower() == 'true',
        }
    elif ai_provider == 'google':
        ai_config = {
            **common_config,
            'api_key': os.environ['GEMINI_API_KEY'],
            'whisper_prompt': os.environ.get('WHISPER_PROMPT', ''),
            'image_model': os.environ.get('IMAGE_MODEL', 'gemini-3-pro-image-preview'),
        }
    else:  # claude
        ai_config = {
            **common_config,
            'api_key': os.environ['CLAUDE_API_KEY'],
            'whisper_prompt': os.environ.get('WHISPER_PROMPT', ''),
        }

    if os.environ.get('MONTHLY_USER_BUDGETS') is not None:
        logging.warning('The environment variable MONTHLY_USER_BUDGETS is deprecated. '
                        'Please use USER_BUDGETS with BUDGET_PERIOD instead.')
    if os.environ.get('MONTHLY_GUEST_BUDGET') is not None:
        logging.warning('The environment variable MONTHLY_GUEST_BUDGET is deprecated. '
                        'Please use GUEST_BUDGET with BUDGET_PERIOD instead.')

    # Load and parse USER_NAMES_DICT from .env
    user_names_json = os.environ.get('USER_NAMES_DICT', '{}') # Default to empty JSON object
    user_names_dict = {}
    try:
        parsed_dict = json.loads(user_names_json)
        # Ensure keys are strings for consistent lookups later
        user_names_dict = {str(k): v for k, v in parsed_dict.items()}
        logging.info(f"Loaded {len(user_names_dict)} user names from USER_NAMES_DICT.")
    except json.JSONDecodeError:
        if user_names_json != '{}': # Log error only if it wasn't the default empty dict
             logging.error(f"Invalid JSON format for USER_NAMES_DICT in .env: {user_names_json}. User names will not be added.")

    telegram_config = {
        'token': os.environ['TELEGRAM_BOT_TOKEN'],
        'admin_user_ids': os.environ.get('ADMIN_USER_IDS', '-'),
        'allowed_user_ids': os.environ.get('ALLOWED_TELEGRAM_USER_IDS', '*'),
        'user_names_dict': user_names_dict,
        'enable_quoting': os.environ.get('ENABLE_QUOTING', 'true').lower() == 'true',
        'enable_latex': os.environ.get('ENABLE_LATEX', 'true').lower() == 'true',
        'enable_image_generation': os.environ.get('ENABLE_IMAGE_GENERATION', 'true').lower() == 'true',
        'enable_transcription': os.environ.get('ENABLE_TRANSCRIPTION', 'true').lower() == 'true',
        'enable_vision': os.environ.get('ENABLE_VISION', 'true').lower() == 'true',
        'enable_tts_generation': os.environ.get('ENABLE_TTS_GENERATION', 'true').lower() == 'true',
        'bot_addressing_words': os.environ.get('BOT_ADDRESSING_WORDS', ''),
        'budget_period': os.environ.get('BUDGET_PERIOD', 'monthly').lower(),
        'user_budgets': os.environ.get('USER_BUDGETS', os.environ.get('MONTHLY_USER_BUDGETS', '*')),
        'guest_budget': float(os.environ.get('GUEST_BUDGET', os.environ.get('MONTHLY_GUEST_BUDGET', '100.0'))),
        'stream': os.environ.get('STREAM', 'true').lower() == 'true',
        'voice_reply_transcript': os.environ.get('VOICE_REPLY_WITH_TRANSCRIPT_ONLY', 'false').lower() == 'true',
        'voice_reply_prompts': os.environ.get('VOICE_REPLY_PROMPTS', '').split(';'),
        'ignore_group_transcriptions': os.environ.get('IGNORE_GROUP_TRANSCRIPTIONS', 'true').lower() == 'true',
        'ignore_group_vision': os.environ.get('IGNORE_GROUP_VISION', 'true').lower() == 'true',
        'group_trigger_keyword': os.environ.get('GROUP_TRIGGER_KEYWORD', ''),
        'group_ignore_keyword': os.environ.get('GROUP_IGNORE_KEYWORD', ''),
        'token_price': float(os.environ.get('TOKEN_PRICE', 0.002)),
        'image_prices': [float(i) for i in os.environ.get('IMAGE_PRICES', "0.016,0.018,0.02").split(",")],
        'vision_token_price': float(os.environ.get('VISION_TOKEN_PRICE', '0.01')),
        'image_receive_mode': os.environ.get('IMAGE_FORMAT', "photo"),
        'tts_model': os.environ.get('TTS_MODEL', 'tts-1'),
        'tts_prices': [float(i) for i in os.environ.get('TTS_PRICES', "0.015,0.030").split(",")],
        'transcription_price': float(os.environ.get('TRANSCRIPTION_PRICE', 0.006)),
        'bot_language': os.environ.get('BOT_LANGUAGE', 'en'),
    }

    plugin_config = {
        'plugins': os.environ.get('PLUGINS', '').split(',')
    }

    # Setup and run AI helper and Telegram bot
    plugin_manager = PluginManager(config=plugin_config)
    
    if ai_provider == 'openai':
        ai_helper = OpenAIHelper2(config=ai_config, plugin_manager=plugin_manager)
    elif ai_provider == 'google':
        ai_helper = GoogleAIHelper(config=ai_config, plugin_manager=plugin_manager)
    else:  # claude
        ai_helper = ClaudeHelper(config=ai_config, plugin_manager=plugin_manager)
    
    telegram_bot = ChatGPTTelegramBot(config=telegram_config, openai=ai_helper)
    telegram_bot.run()


if __name__ == '__main__':
    main()
