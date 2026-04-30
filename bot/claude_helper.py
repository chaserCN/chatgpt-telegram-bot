from __future__ import annotations

import asyncio
import base64
import datetime
import logging
from typing import Dict

import anthropic
from rich.console import Console
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from constants import MULTIUSER_CHAT_INSTRUCTIONS
from plugin_manager import PluginManager
from utils import localized_text

console = Console()


class ClaudeHelper:
    def __init__(self, config: dict, plugin_manager: PluginManager):
        self.client = anthropic.AsyncAnthropic(api_key=config['api_key'])
        self.config = config
        self.plugin_manager = plugin_manager
        self.session_ids: dict[int, str] = {}
        self.seen_event_ids: dict[int, set[str]] = {}
        self.last_updated: dict[int, datetime.datetime] = {}
        self.managed_agent_id: str | None = config.get('managed_agent_id')
        self.managed_environment_id: str | None = config.get('managed_environment_id')
        self._resource_lock = asyncio.Lock()

    def reset_chat_history(self, chat_id: int, content=''):
        """Reset the Claude managed session for a specific chat."""
        session_id = self.session_ids.pop(chat_id, None)
        self.seen_event_ids.pop(chat_id, None)
        self.last_updated.pop(chat_id, None)
        if session_id:
            asyncio.create_task(self.__delete_session(session_id))

    def __max_age_reached(self, chat_id: int) -> bool:
        if chat_id not in self.last_updated:
            return True
        age = datetime.datetime.now() - self.last_updated[chat_id]
        return age.total_seconds() > self.config['max_conversation_age_minutes'] * 60

    #########################################################
    # Chat model
    #########################################################

    async def get_chat_response(self, chat_id: int, query: str, user_name: str | None) -> Dict | str:
        logging.info(f'[CLAUDE NON-STREAM] Starting get_chat_response: chat_id={chat_id}, query="{query[:50]}..."')
        answer = ""
        has_web_search = False

        async for chunk, is_final, used_web_search in self.__run_turn_stream(
            chat_id=chat_id,
            content_blocks=self.__text_content_blocks(query, user_name),
        ):
            answer = chunk
            has_web_search = used_web_search
            if is_final:
                break

        if not answer.strip():
            answer = localized_text('empty_response', self.config['bot_language'])

        if has_web_search:
            web_search_prefix = localized_text('web_search_result', self.config['bot_language'])
            answer = f"<i>{web_search_prefix}</i>\n\n{answer}"

        console.print(
            f"[bold green]✅ [CLAUDE NON-STREAM] Final result:[/bold green] "
            f"[cyan]length={len(answer)}, web_search={has_web_search}[/cyan]"
        )
        return answer

    async def get_chat_response_stream(self, chat_id: int, query: str, user_name: str | None) -> tuple[str, bool]:
        logging.info(f'[CLAUDE STREAM] Starting get_chat_response_stream: chat_id={chat_id}, query="{query[:50]}..."')

        last_answer = ""
        last_has_web_search = False
        async for answer, is_final, has_web_search in self.__run_turn_stream(
            chat_id=chat_id,
            content_blocks=self.__text_content_blocks(query, user_name),
        ):
            if is_final:
                if not answer.strip():
                    answer = localized_text('empty_response', self.config['bot_language'])
                if has_web_search:
                    web_search_prefix = localized_text('web_search_result', self.config['bot_language'])
                    answer = f"<i>{web_search_prefix}</i>\n\n{answer}"
                yield answer, True
                return

            last_answer = answer
            last_has_web_search = has_web_search
            yield answer, False

        final_answer = last_answer or localized_text('empty_response', self.config['bot_language'])
        if last_has_web_search:
            web_search_prefix = localized_text('web_search_result', self.config['bot_language'])
            final_answer = f"<i>{web_search_prefix}</i>\n\n{final_answer}"
        yield final_answer, True

    def reset_conversation(self, chat_id: int):
        self.reset_chat_history(chat_id)

    #########################################################
    # Vision
    #########################################################

    async def interpret_image(self, chat_id: int, fileobj, user_name: str | None, prompt=None, use_image_model=False) -> str | Dict:
        logging.info(f'[CLAUDE VISION] Vision request: prompt="{prompt}", user_name={user_name}')
        answer = ""

        async for chunk, is_final, _ in self.__run_turn_stream(
            chat_id=chat_id,
            content_blocks=self.__image_content_blocks(fileobj, user_name, prompt),
        ):
            answer = chunk
            if is_final:
                break

        return answer or localized_text('empty_response', self.config['bot_language'])

    async def interpret_image_stream(self, chat_id: int, fileobj, user_name: str | None, prompt=None, use_image_model=False) -> tuple[str, bool, bool]:
        logging.info(f'[CLAUDE VISION] Starting interpret_image_stream: prompt="{prompt}", user_name={user_name}')

        async for answer, is_final, _ in self.__run_turn_stream(
            chat_id=chat_id,
            content_blocks=self.__image_content_blocks(fileobj, user_name, prompt),
        ):
            if is_final and not answer.strip():
                answer = localized_text('empty_response', self.config['bot_language'])
            yield answer, is_final, is_final

    #########################################################
    # Managed Agents
    #########################################################

    @retry(
        reraise=True,
        retry=retry_if_exception_type(Exception),
        wait=wait_fixed(20),
        stop=stop_after_attempt(3)
    )
    async def __run_turn_stream(self, chat_id: int, content_blocks: list[dict]):
        bot_language = self.config['bot_language']
        try:
            session_id = await self.__ensure_session(chat_id)
            await self.client.beta.sessions.events.send(
                session_id,
                events=[{
                    "type": "user.message",
                    "content": content_blocks,
                }],
            )
            self.last_updated[chat_id] = datetime.datetime.now()

            answer = ""
            has_web_search = False
            chunk_count = 0

            event_stream = await self.client.beta.sessions.events.stream(session_id)
            async for event in event_stream:
                event_id = getattr(event, 'id', None)
                if event_id and event_id in self.seen_event_ids.setdefault(chat_id, set()):
                    continue
                if event_id:
                    self.seen_event_ids.setdefault(chat_id, set()).add(event_id)

                chunk_count += 1
                event_type = getattr(event, 'type', '')

                if event_type == "agent.message":
                    answer += self.__extract_text_from_blocks(getattr(event, 'content', []) or [])
                    logging.info(f'[CLAUDE STREAM] Chunk {chunk_count}: total_length={len(answer)}')
                    yield answer, False, has_web_search
                elif event_type == "agent.tool_use":
                    tool_name = getattr(event, 'name', '')
                    if 'search' in tool_name:
                        has_web_search = True
                        logging.info(f'[CLAUDE STREAM] Web search detected in chunk {chunk_count}')
                elif event_type == "session.error":
                    error_obj = getattr(event, 'error', None)
                    error_type = getattr(error_obj, 'type', 'unknown_error')
                    error_message = getattr(error_obj, 'message', None) or getattr(event, 'message', None) or 'Managed session error'
                    logging.error(
                        "[CLAUDE STREAM] session.error: type=%s, message=%s, event=%r",
                        error_type,
                        error_message,
                        event,
                    )
                    raise Exception(f"{error_type}: {error_message}")
                elif event_type == "session.status_idle":
                    stop_reason = getattr(event, 'stop_reason', None)
                    stop_type = getattr(stop_reason, 'type', None)
                    if stop_type == "requires_action":
                        raise Exception("Claude session requires manual tool confirmation")
                    logging.info(f'[CLAUDE STREAM] Final chunk {chunk_count} received')
                    break

            console.print(
                f"[bold blue]🔄 [CLAUDE STREAM] Streaming completed:[/bold blue] "
                f"[cyan]chunks={chunk_count}, response_length={len(answer)}, web_search={has_web_search}[/cyan]"
            )
            yield answer, True, has_web_search

        except Exception as e:
            logging.error(f'[CLAUDE TURN] Error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def __ensure_session(self, chat_id: int) -> str:
        if self.__max_age_reached(chat_id):
            self.reset_chat_history(chat_id)

        session_id = self.session_ids.get(chat_id)
        if session_id:
            return session_id

        await self.__ensure_managed_resources()
        session = await self.client.beta.sessions.create(
            agent=self.managed_agent_id,
            environment_id=self.managed_environment_id,
            title=f"Telegram chat {chat_id}",
        )
        self.session_ids[chat_id] = session.id
        self.seen_event_ids[chat_id] = set()
        self.last_updated[chat_id] = datetime.datetime.now()
        return session.id

    async def __ensure_managed_resources(self) -> None:
        if self.managed_agent_id and self.managed_environment_id:
            return

        async with self._resource_lock:
            suffix = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
            if not self.managed_environment_id:
                environment = await self.client.beta.environments.create(
                    name=f"{self.config.get('managed_environment_name', 'telegram-claude-env')}-{suffix}",
                    config={
                        "type": "cloud",
                        "networking": {"type": "unrestricted"},
                    },
                )
                self.managed_environment_id = environment.id
                logging.info(f'[CLAUDE MANAGED] Created environment: {environment.id}')

            if not self.managed_agent_id:
                system_instruction = MULTIUSER_CHAT_INSTRUCTIONS
                if self.config.get('assistant_prompt'):
                    system_instruction += self.config['assistant_prompt']

                agent = await self.client.beta.agents.create(
                    name=f"{self.config.get('managed_agent_name', 'Telegram Claude Agent')} {suffix}",
                    model={"id": self.config['model']},
                    system=system_instruction,
                    tools=[{"type": "agent_toolset_20260401"}],
                )
                self.managed_agent_id = agent.id
                logging.info(f'[CLAUDE MANAGED] Created agent: {agent.id}')

    async def __delete_session(self, session_id: str) -> None:
        try:
            await self.client.beta.sessions.delete(session_id)
        except Exception as e:
            logging.warning(f'[CLAUDE MANAGED] Failed to delete session {session_id}: {e}')

    #########################################################
    # Helpers
    #########################################################

    def __text_content_blocks(self, query: str, user_name: str | None) -> list[dict]:
        if user_name:
            query = f"{user_name}: {query}"
        return [{"type": "text", "text": query}]

    def __image_content_blocks(self, fileobj, user_name: str | None, prompt: str | None) -> list[dict]:
        image_bytes = fileobj.getvalue()
        image_data = base64.b64encode(image_bytes).decode('utf-8')
        speaker = user_name or "User"

        if image_bytes.startswith(b'\x89PNG'):
            media_type = "image/png"
        elif image_bytes.startswith(b'\xff\xd8\xff'):
            media_type = "image/jpeg"
        elif image_bytes.startswith(b'RIFF') and b'WEBP' in image_bytes[:12]:
            media_type = "image/webp"
        elif image_bytes.startswith(b'GIF87a') or image_bytes.startswith(b'GIF89a'):
            media_type = "image/gif"
        else:
            media_type = "image/jpeg"

        if not prompt or not prompt.strip():
            prompt = self.config['vision_prompt']

        if user_name and prompt.strip():
            prompt = f"{user_name}: {prompt}"
        else:
            prompt = f"{speaker} sends a picture. {prompt}"

        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                },
            },
            {"type": "text", "text": prompt},
        ]

    def __extract_text_from_blocks(self, blocks) -> str:
        parts: list[str] = []
        for block in blocks:
            if getattr(block, 'type', '') == 'text' and getattr(block, 'text', None):
                parts.append(block.text)
        return ''.join(parts)

    #########################################################
    # Image model (Not supported by Claude natively)
    #########################################################

    async def generate_image(self, prompt: str) -> tuple[str, str]:
        bot_language = self.config['bot_language']
        raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\nImage generation is not supported by Claude API. Consider using other models for image generation.")

    #########################################################
    # TTS/Whisper model (Not supported by Claude natively)
    #########################################################

    async def generate_speech(self, text: str) -> tuple[any, int]:
        bot_language = self.config['bot_language']
        raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\nText-to-speech is not supported by Claude API. Consider using other models for TTS.")

    async def transcribe(self, filename, prompt=None):
        bot_language = self.config['bot_language']
        raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\nAudio transcription is not supported by Claude API. Consider using other models for transcription.")
