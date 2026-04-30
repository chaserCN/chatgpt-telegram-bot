from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import base64
import random
import string

import telegram
from telegram import Message, MessageEntity, Update, ChatMember, constants
from telegram.ext import CallbackContext, ContextTypes

from latex.latex_handler import process_text
from usage_tracker import UsageTracker

try:
    from rich import print as rprint
    from rich.pretty import pprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


# Load translations
parent_dir_path = os.path.join(os.path.dirname(__file__), os.pardir)
translations_file_path = os.path.join(parent_dir_path, 'translations.json')
with open(translations_file_path, 'r', encoding='utf-8') as f:
    translations = json.load(f)

def localized_text(key, bot_language):
    """
    Return translated text for a key in specified bot_language.
    Keys and translations can be found in the translations.json.
    """
    try:
        return translations[bot_language][key]
    except KeyError:
        logging.warning(f"No translation available for bot_language code '{bot_language}' and key '{key}'")
        # Fallback to English if the translation is not available
        if key in translations['en']:
            return translations['en'][key]
        else:
            logging.warning(f"No english definition found for key '{key}' in translations.json")
            # return key as text
            return key



def message_text(message: Message) -> str:
    """
    Returns the text of a message, excluding any bot commands.
    """
    message_txt = message.text
    if message_txt is None:
        return ''

    for _, text in sorted(message.parse_entities([MessageEntity.BOT_COMMAND]).items(),
                          key=(lambda item: item[0].offset)):
        message_txt = message_txt.replace(text, '').strip()

    return message_txt if len(message_txt) > 0 else ''


async def is_user_in_group(update: Update, context: CallbackContext, user_id: int) -> bool:
    """
    Checks if user_id is a member of the group
    """
    try:
        chat_member = await context.bot.get_chat_member(update.message.chat_id, user_id)
        return chat_member.status in [ChatMember.OWNER, ChatMember.ADMINISTRATOR, ChatMember.MEMBER]
    except telegram.error.BadRequest as e:
        if str(e) == "User not found":
            return False
        else:
            raise e
    except Exception as e:
        raise e


def get_thread_id(update: Update) -> int | None:
    """
    Gets the message thread id for the update, if any
    """
    if update.effective_message and update.effective_message.is_topic_message:
        return update.effective_message.message_thread_id
    return None


def get_stream_cutoff_values(update: Update, content: str) -> int:
    """
    Gets the stream cutoff values for the message length
    """
    if is_group_chat(update):
        # group chats have stricter flood limits
        return 180 if len(content) > 1000 else 120 if len(content) > 200 \
            else 90 if len(content) > 50 else 50
    return 90 if len(content) > 1000 else 45 if len(content) > 200 \
        else 25 if len(content) > 50 else 15


def is_group_chat(update: Update) -> bool:
    """
    Checks if the message was sent from a group chat
    """
    if not update.effective_chat:
        return False
    return update.effective_chat.type in [
        constants.ChatType.GROUP,
        constants.ChatType.SUPERGROUP
    ]


def split_into_chunks(text: str, chunk_size: int = 4096) -> list[str]:
    """
    Splits a string into chunks of a given size.
    """
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


async def wrap_with_indicator(update: Update, context: CallbackContext, coroutine,
                              chat_action: constants.ChatAction = "", is_inline=False):
    """
    Wraps a coroutine while repeatedly sending a chat action to the user.
    """
    task = context.application.create_task(coroutine(), update=update)
    while not task.done():
        if not is_inline:
            context.application.create_task(
                update.effective_chat.send_action(chat_action, message_thread_id=get_thread_id(update))
            )
        try:
            await asyncio.wait_for(asyncio.shield(task), 4.5)
        except asyncio.TimeoutError:
            pass

async def send_action_periodically(update: Update, context: CallbackContext, chat_action: constants.ChatAction = ""):
    while True:
        context.application.create_task(
            update.effective_chat.send_action(chat_action, message_thread_id=get_thread_id(update))
        )
        await asyncio.sleep(4.5)

async def send_message_with_retry(update: Update,
                                  text: str, markdown: bool = True, reply_to_message_id: int | None = None,
                                  message_thread_id: int | None = None, enable_latex: bool = True):
    """
    Sends a message with a robust fallback mechanism for HTML parsing errors.
    1. Sanitizes the text using fix_telegram_html_formatting and tries to send it.
    2. On failure, strips all tags with remove_html_tags and sends as plain text.
    """
    # Validate inputs
    if update is None or update.effective_message is None:
        raise ValueError("Update or update.effective_message cannot be None")
    if text is None:
        text = ""

    try:
        # Step 1: Always sanitize the text first and try sending.
        fixed_text = fix_telegram_html_formatting(text)

        logging.debug("sanitized text length=%s", len(fixed_text))
        
        # Process for LaTeX if enabled
        if enable_latex:
            processed_type, processed_content = process_text(fixed_text, output_dir='uploads')
            
            if processed_type == 'image':
                sent_messages = []
                for image_path in processed_content:
                    try:
                        with open(image_path, 'rb') as photo_file:
                            message = await update.effective_message.reply_photo(
                                photo=photo_file,
                                reply_to_message_id=reply_to_message_id,
                                message_thread_id=message_thread_id
                            )
                        sent_messages.append(message)
                    finally:
                        if os.path.exists(image_path):
                            os.remove(image_path)
                return sent_messages[-1] if sent_messages else None

            # If it's text, update fixed_text with the (potentially unchanged) text
            fixed_text = processed_content
        
        # Chunk the text and send each chunk
        chunks = split_into_chunks(fixed_text)
        sent_messages = []
        for i, chunk in enumerate(chunks):
            reply_id = reply_to_message_id if i == 0 else None
            message = await update.effective_message.reply_text(
                text=chunk,
                parse_mode=constants.ParseMode.HTML if markdown else None,
                reply_to_message_id=reply_id,
                message_thread_id=message_thread_id
            )
            sent_messages.append(message)
        return sent_messages[-1] if sent_messages else None

    except telegram.error.BadRequest as e:
        logging.warning(f"BadRequest after sanitization: {e}. Falling back to plain text.")

        try:
            # Step 2 (Fallback): Strip all tags and send as plain text.
            stripped_text = remove_html_tags(text)
            chunks = split_into_chunks(stripped_text)
            sent_messages = []
            for i, chunk in enumerate(chunks):
                reply_id = reply_to_message_id if i == 0 else None
                message = await update.effective_message.reply_text(
                    text=chunk,
                    parse_mode=None, # Plain text, no HTML
                    reply_to_message_id=reply_id,
                    message_thread_id=message_thread_id
                )
                sent_messages.append(message)
            return sent_messages[-1] if sent_messages else None

        except Exception as e2:
            logging.error(f"Final fallback failed: {e2}. Original text was:\n---\n{text}\n---")
            raise e2  # Re-raise the final, critical error

    except Exception as e:
        logging.error(f'An unexpected exception occurred in send_message_with_retry: {e}')
        raise e

async def edit_message_with_retry(context: ContextTypes.DEFAULT_TYPE, chat_id: int | None,
                                  message_id: str, text: str, markdown: bool = True, is_inline: bool = False):
    """
    Edit a message with retry logic in case of failure (e.g. broken markdown)
    :param context: The context to use
    :param chat_id: The chat id to edit the message in
    :param message_id: The message id to edit
    :param text: The text to edit the message with
    :param markdown: Whether to use markdown parse mode
    :param is_inline: Whether the message to edit is an inline message
    :return: None
    """
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(message_id) if not is_inline else None,
            inline_message_id=message_id if is_inline else None,
            text=text,
            parse_mode=constants.ParseMode.HTML if markdown else None,
        )
    except telegram.error.BadRequest as e:
        logging.warning("BadRequest: " + str(e)) 

        if str(e).startswith("Message is not modified"):
            return
        try:
            text = remove_html_tags(text)

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id) if not is_inline else None,
                inline_message_id=message_id if is_inline else None,
                text=text,
            )
        except Exception as e:
            logging.warning(f'Failed to edit message: {str(e)}')
            raise e

    except Exception as e:
        logging.warning("Exception: " + str(e))
        raise e

def _fix_entities(text: str) -> str:
    """
    Властивості:
      1) Не чіпає HTML-теги (вміст у <> лишається як є).
      2) У тексті між тегами:
         - перетворює сирі < > & " у &lt; &gt; &amp; &quot; (лише ці 4, як в Telegram),
         - декодує всі інші ентіті (напр. &infin;, &#60;, &#x221E;) у символи,
           але зберігає &lt; &gt; &amp; &quot; як ентіті.

    Приклади:
      "<b>5 < 10</b>"            -> "<b>5 &lt; 10</b>"
      "<b>5 &lt; 10<b>"          -> "<b>5 &lt; 10<b>"
      "<b>-&infin; &lt; 10<b>"   -> "<b>-∞ &lt; 10<b>"
      "<a href=\"x?y=1&z=2\">go</a>"  -> без змін
    """
    if not text:
        return text

    import re, html

    # --- знайти коректні теги (кутова дужка '>' завершує тег лише поза лапками)
    def _extract_tags_spans(s: str):
        spans = []
        n = len(s)
        i = 0
        while i < n:
            if s[i] == '<' and i + 1 < n and (s[i+1].isalpha() or s[i+1] == '/'):
                j = i + 1
                in_sq = False
                in_dq = False
                while j < n:
                    ch = s[j]
                    if ch == '"' and not in_sq:
                        in_dq = not in_dq
                    elif ch == "'" and not in_dq:
                        in_sq = not in_sq
                    elif ch == '>' and not in_sq and not in_dq:
                        spans.append((i, j + 1))
                        i = j + 1
                        break
                    j += 1
                else:
                    # якщо тег не закрився — вважаємо це текстом
                    i += 1
            else:
                i += 1
        return spans

    spans = _extract_tags_spans(text)

    # --- підміняємо теги плейсхолдерами
    placeholders = {}
    parts = []
    last = 0
    for idx, (start, end) in enumerate(spans):
        ph = f"__HTML_TAG_{idx}__"
        placeholders[ph] = text[start:end]
        parts.append(text[last:start])
        parts.append(ph)
        last = end
    parts.append(text[last:])
    temp_text = "".join(parts)

    # --- не даємо html.unescape з'їсти безкрапкові &lt &gt &amp &quot (рідкі кейси)
    for name in ("lt", "gt", "amp", "quot"):
        temp_text = re.sub(rf"&{name}(?!;)", rf"&amp;{name}", temp_text, flags=re.IGNORECASE)

    # --- зберігаємо дозволені ентіті
    allowed_entities = {
        "&lt;": "___KEEP_LT___",
        "&gt;": "___KEEP_GT___",
        "&amp;": "___KEEP_AMP___",
        "&quot;": "___KEEP_QUOT___",
    }
    for ent, ph in allowed_entities.items():
        temp_text = temp_text.replace(ent, ph)

    # --- роздекодовуємо решту ентіті у символи (напр. &infin; -> ∞, &#60; -> <)
    temp_text = html.unescape(temp_text)

    # --- тимчасово захищаємо будь-які інші валідні ентіті (&foo;, &#123;, &#x1F4A9;)
    entity_like_pattern = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#[0-9]+|#x[0-9A-Fa-f]+);")
    entity_placeholders = {}
    i = 0
    def _ent_repl(m):
        nonlocal i
        token = f"__ENTITY_PH_{i}__"
        entity_placeholders[token] = m.group(0)
        i += 1
        return token
    temp_text = entity_like_pattern.sub(_ent_repl, temp_text)

    # --- екрануємо лише 4 дозволені символи у тексті
    temp_text = (
        temp_text.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;")
    )

    # --- повертаємо інші валідні ентіті як були
    for ph, ent in entity_placeholders.items():
        temp_text = temp_text.replace(ph, ent)

    # --- повертаємо дозволені ентіті
    for ent, ph in allowed_entities.items():
        temp_text = temp_text.replace(ph, ent)

    # --- повертаємо теги
    for ph, tag in placeholders.items():
        temp_text = temp_text.replace(ph, tag)

    return temp_text


def remove_html_tags(text: str) -> str:
    """
    Strips all HTML tags from the text and correctly handles HTML entities.
    """
    if not text:
        return text
    
    import re

    # Compiles a regular expression to find and remove any HTML tag.
    clean = re.compile('<.*?>')
    text = re.sub(clean, '', text)
    
    # Processes HTML entities to ensure they are correctly represented.
    text = _fix_entities(text)
    
    return text


def fix_telegram_html_formatting(text: str) -> str:
    """
    Sanitizes AI-generated text to comply with Telegram's strict HTML subset rules using an allowlist-based approach.
    """
    if not text:
        return ""

    import re
    import html

    # Use a robust HTML parser like BeautifulSoup to correctly handle tag matching and attributes.
    # This is much safer than regex for complex HTML.
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("Warning: BeautifulSoup4 is not installed. Using a less robust regex-based HTML sanitizer.")
        return re.sub(r'<[^>]*>', '', text)

    # Step 1: Remove backticks, as they are not supported in Telegram's HTML.
    text = text.replace('`', '')

    # Defines the tags and their allowed attributes based on Telegram's formatting rules.
    allowed_tags = {
        'b': [], 'strong': [], 'i': [], 'em': [], 'u': [], 'ins': [],
        's': [], 'strike': [], 'del': [], 'tg-spoiler': [],
        'a': ['href'],
        'span': ['class'],
        'code': [],
        'pre': [],
        'blockquote': []
    }

    # Use 'html.parser' for its standard library availability and speed.
    soup = BeautifulSoup(text, 'html.parser')

    for tag in soup.find_all(True):
        # but rather convert it back to an escaped plain string. This way,
        # it's guaranteed to be treated as text by Telegram.
        if tag.name not in allowed_tags:
            tag.replace_with(html.escape(str(tag)))
            continue

        # --- Sanitize Attributes ---
        allowed_attrs = allowed_tags.get(tag.name, [])
        current_attrs = dict(tag.attrs)

        for attr_name, attr_value in current_attrs.items():
            if attr_name not in allowed_attrs:
                del tag[attr_name]
                continue
            
            if tag.name == 'a' and attr_name == 'href':
                if not (isinstance(attr_value, str) and (attr_value.startswith('http') or attr_value.startswith('tg:'))):
                    del tag[attr_name]
            elif tag.name == 'span' and attr_name == 'class':
                if not (isinstance(attr_value, list) and 'tg-spoiler' in attr_value):
                    del tag[attr_name]
        
        # After sanitizing attributes, we must also escape the content of pre/code tags
        if tag.name in ['pre', 'code']:
            # Use html.escape on the tag's string content to handle <, >, &
            # This prevents Telegram from interpreting them as tags.
            original_string = tag.string
            if original_string:
                tag.string = html.escape(original_string)
        
        if tag.name in ['a', 'span'] and not tag.attrs:
            tag.unwrap()

    # After initial sanitization, specifically handle standalone `<code>` tags.
    # We wrap them in `<i>` for consistent rendering in Telegram.
    for code_tag in soup.find_all('code'):
        # Check if the parent is not a <pre> tag
        if code_tag.parent.name != 'pre':
            # To wrap it, we create a new `<i>` tag and replace the `<code>` tag with it.
            # The contents of `<code>` are moved inside the new `<i>` tag.
            new_tag = soup.new_tag('i')
            # Ensure we handle NavigableString and other tags correctly
            if code_tag.string:
                new_tag.string = code_tag.string
                code_tag.replace_with(new_tag)

    # Convert the sanitized soup back to a string.
    # The formatter=None argument prevents bs4 from adding extra HTML structure.
    sanitized_html = soup.decode(formatter=None)

    # Final entity processing to ensure only allowed entities remain and stray chars are escaped.
    return _fix_entities(sanitized_html)


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles errors in the telegram-python-bot library.
    """
    logging.error(f'Exception while handling an update: {context.error}')


async def is_allowed(config, update: Update, context: CallbackContext, is_inline=False) -> bool:
    """
    Checks if the user is allowed to use the bot.
    """
    if config['allowed_user_ids'] == '*':
        return True

    user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id
    if is_admin(config, user_id):
        return True
    name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
    allowed_user_ids = config['allowed_user_ids'].split(',')
    # Check if user is allowed
    if str(user_id) in allowed_user_ids:
        return True
    # Check if it's a group a chat with at least one authorized member
    if not is_inline and is_group_chat(update):
        admin_user_ids = config['admin_user_ids'].split(',')
        for user in itertools.chain(allowed_user_ids, admin_user_ids):
            if not user.strip():
                continue
            if await is_user_in_group(update, context, user):
                logging.info(f'{user} is a member. Allowing group chat message...')
                return True
        logging.info(f'Group chat messages from user {name} '
                     f'(id: {user_id}) are not allowed')
    return False


def is_admin(config, user_id: int, log_no_admin=False) -> bool:
    """
    Checks if the user is the admin of the bot.
    The first user in the user list is the admin.
    """
    if config['admin_user_ids'] == '-':
        if log_no_admin:
            logging.info('No admin user defined.')
        return False

    admin_user_ids = config['admin_user_ids'].split(',')

    # Check if user is in the admin user list
    if str(user_id) in admin_user_ids:
        return True

    return False


def get_user_budget(config, user_id) -> float | None:
    """
    Get the user's budget based on their user ID and the bot configuration.
    :param config: The bot configuration object
    :param user_id: User id
    :return: The user's budget as a float, or None if the user is not found in the allowed user list
    """

    # no budget restrictions for admins and '*'-budget lists
    if is_admin(config, user_id) or config['user_budgets'] == '*':
        return float('inf')

    user_budgets = config['user_budgets'].split(',')
    if config['allowed_user_ids'] == '*':
        # same budget for all users, use value in first position of budget list
        if len(user_budgets) > 1:
            logging.warning('multiple values for budgets set with unrestricted user list '
                            'only the first value is used as budget for everyone.')
        return float(user_budgets[0])

    allowed_user_ids = config['allowed_user_ids'].split(',')
    if str(user_id) in allowed_user_ids:
        user_index = allowed_user_ids.index(str(user_id))
        if len(user_budgets) <= user_index:
            logging.warning(f'No budget set for user id: {user_id}. Budget list shorter than user list.')
            return 0.0
        return float(user_budgets[user_index])
    return None


def get_remaining_budget(config, usage, update: Update, is_inline=False) -> float:
    """
    Calculate the remaining budget for a user based on their current usage.
    :param config: The bot configuration object
    :param usage: The usage tracker object
    :param update: Telegram update object
    :param is_inline: Boolean flag for inline queries
    :return: The remaining budget for the user as a float
    """
    # Mapping of budget period to cost period
    budget_cost_map = {
        "monthly": "cost_month",
        "daily": "cost_today",
        "all-time": "cost_all_time"
    }

    user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id
    name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
    if user_id not in usage:
        usage[user_id] = UsageTracker(user_id, name)

    # Get budget for users
    user_budget = get_user_budget(config, user_id)
    budget_period = config['budget_period']
    if user_budget is not None:
        cost = usage[user_id].get_current_cost()[budget_cost_map[budget_period]]
        return user_budget - cost

    # Get budget for guests
    if 'guests' not in usage:
        usage['guests'] = UsageTracker('guests', 'all guest users in group chats')
    cost = usage['guests'].get_current_cost()[budget_cost_map[budget_period]]
    return config['guest_budget'] - cost


def is_within_budget(config, usage, update: Update, is_inline=False) -> bool:
    """
    Checks if the user reached their usage limit.
    Initializes UsageTracker for user and guest when needed.
    :param config: The bot configuration object
    :param usage: The usage tracker object
    :param update: Telegram update object
    :param is_inline: Boolean flag for inline queries
    :return: Boolean indicating if the user has a positive budget
    """
    user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id
    name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
    if user_id not in usage:
        usage[user_id] = UsageTracker(user_id, name)
    remaining_budget = get_remaining_budget(config, usage, update, is_inline=is_inline)
    return remaining_budget > 0


def get_reply_to_message_id(config, update: Update):
    """
    Returns the message id of the message to reply to
    :param config: Bot configuration object
    :param update: Telegram update object
    :return: Message id of the message to reply to, or None if quoting is disabled
    """
    if config['enable_quoting'] or is_group_chat(update):
        return update.message.message_id
    return None


def is_direct_result(response: any) -> bool:
    """
    Checks if the dict contains a direct result that can be sent directly to the user
    :param response: The response value
    :return: Boolean indicating if the result is a direct result
    """
    if type(response) is not dict:
        try:
            json_response = json.loads(response)
            return json_response.get('direct_result', False)
        except:
            return False
    else:
        return response.get('direct_result', False)


def direct_result_kind(response: any) -> str:
    """
    Returns the kind of direct_result from response.
    Returns 'result' if not a direct_result or kind not found.
    :param response: The response value
    :return: String indicating the kind of direct result
    """
    if isinstance(response, dict) and 'direct_result' in response:
        return response['direct_result'].get('kind', 'result')
    
    # Handle case when response is a string (JSON)
    if isinstance(response, str):
        try:
            json_response = json.loads(response)
            if 'direct_result' in json_response:
                return json_response['direct_result'].get('kind', 'result')
        except:
            pass
    
    return 'result'



async def handle_direct_result(config, update: Update, response: any):
    """
    Handles a direct result from a plugin
    """
    if type(response) is not dict:
        response = json.loads(response)

    result = response['direct_result']
    kind = result['kind']
    format = result['format']
    value = result['value']

    common_args = {
        'message_thread_id': get_thread_id(update),
        'reply_to_message_id': get_reply_to_message_id(config, update),
    }

    if kind == 'photo':
        if format == 'url':
            await update.effective_message.reply_photo(**common_args, photo=value)
        elif format == 'path':
            await update.effective_message.reply_photo(**common_args, photo=open(value, 'rb'))
    elif kind == 'gif' or kind == 'file':
        if format == 'url':
            await update.effective_message.reply_document(**common_args, document=value)
        if format == 'path':
            await update.effective_message.reply_document(**common_args, document=open(value, 'rb'))
    elif kind == 'dice':
        await update.effective_message.reply_dice(**common_args, emoji=value)

    if format == 'path':
        cleanup_intermediate_files(response)


def cleanup_intermediate_files(response: any):
    """
    Deletes intermediate files created by plugins
    """
    if type(response) is not dict:
        response = json.loads(response)

    result = response['direct_result']
    format = result['format']
    value = result['value']

    if format == 'path':
        if os.path.exists(value):
            os.remove(value)


# Function to encode the image
def encode_image(fileobj):
    image = base64.b64encode(fileobj.getvalue()).decode('utf-8')
    return f'data:image/jpeg;base64,{image}'

def decode_image(imgbase64):
    image = imgbase64[len('data:image/jpeg;base64,'):]
    return base64.b64decode(image)

def generate_random_string(length):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def random_file_name(directory_name, extension):
    if not os.path.exists(directory_name):
        os.makedirs(directory_name)
    return os.path.join(directory_name, f"{generate_random_string(15)}.{extension}")

def print_object(title, obj):
    """
    Print an object in a clean format using rich if available, otherwise JSON
    """
    obj_data = {attr: getattr(obj, attr, None) for attr in dir(obj) 
                if not attr.startswith('_') and not attr.startswith('model_') and not callable(getattr(obj, attr, None))}
    
    if RICH_AVAILABLE:
        print(title)
        pprint(obj_data)
    else:
        print(f"{title}: {json.dumps(obj_data, indent=2, ensure_ascii=False, default=str)}")

