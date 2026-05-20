from __future__ import annotations
import datetime
import json
import logging
import base64
import io
import wave
import subprocess
import tempfile
import os
from typing import Dict

from google import genai
from google.genai import types
import io
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from rich.console import Console

from plugin_manager import PluginManager
from utils import localized_text, random_file_name
from constants import MULTIUSER_CHAT_INSTRUCTIONS

console = Console()

class GoogleAIHelper:
    def __init__(self, config: dict, plugin_manager: PluginManager):
        self.client = genai.Client(api_key=config['api_key'])
        self.config = config
        self.plugin_manager = plugin_manager
        self.previous_interactions: dict[int: str] = {}  # {chat_id: interaction_id}
        self.last_updated: dict[int: datetime] = {}  # {chat_id: last_update_timestamp} 

    def reset_chat_history(self, chat_id: int, content=''):
        """Reset the interaction history for a specific chat"""
        if chat_id in self.previous_interactions:
            del self.previous_interactions[chat_id]

    def __max_age_reached(self, chat_id: int) -> bool:
        """Check if the maximum interaction age has been reached"""
        if chat_id not in self.last_updated:
            return True
        age = datetime.datetime.now() - self.last_updated[chat_id]
        return age.total_seconds() > self.config['max_conversation_age_minutes'] * 60

    #########################################################
    # Chat model
    #########################################################

    async def get_chat_response(self, chat_id: int, query: str, user_name: str | None) -> Dict | str:
        logging.info(f'[NON-STREAM] Starting get_chat_response: chat_id={chat_id}, query="{query[:50]}..."')        
        
        response = await self.__send_query(chat_id, query, user_name, stream=False)    
        
        return await self.__process_nonstreaming_response(response, chat_id)

    async def get_chat_response_stream(self, chat_id: int, query: str, user_name: str | None) -> tuple[str | Dict, bool]:
        logging.info(f'[STREAM] Starting get_chat_response_stream: chat_id={chat_id}, query="{query[:50]}..."')

        response = await self.__send_query(chat_id, query, user_name, stream=True)
        
        async for answer, is_final in self.__process_streaming_response(response, chat_id):
            yield answer, is_final

    # @retry(
    #     reraise=True,
    #     retry=retry_if_exception_type(Exception),
    #     wait=wait_fixed(20),
    #     stop=stop_after_attempt(3)
    # )
    async def __send_query(self, chat_id: int, query: str, user_name: str | None, stream=False):
        bot_language = self.config['bot_language']
        try:
            if self.__max_age_reached(chat_id):
                self.reset_chat_history(chat_id)

            self.last_updated[chat_id] = datetime.datetime.now()

            if user_name:
                query = f"{user_name}: {query}"

            instructions = MULTIUSER_CHAT_INSTRUCTIONS
            if 'assistant_prompt' in self.config and self.config['assistant_prompt']:
                instructions += self.config['assistant_prompt']

            tools = self.__tools_for_request()
            has_previous_interaction = chat_id in self.previous_interactions

            console.print(
                f"[bold yellow]🚀 [SEND] API request:[/bold yellow] "
                f"[cyan]model={self.config['model']}, stream={stream}, has_previous_interaction={has_previous_interaction}[/cyan]"
            )
            logging.info(
                f'[SEND] API request: model={self.config["model"]}, stream={stream}, '
                f'has_previous_interaction={has_previous_interaction}'
            )
            interaction_args = self.__interaction_request_args(
                chat_id=chat_id,
                input_payload=query,
                system_instruction=instructions,
                tools=tools,
                stream=stream,
            )

            self.__log_google_request('interactions.create.chat', **interaction_args)
            response = await self.client.aio.interactions.create(**interaction_args)

            logging.info(f'[SEND] Response received: type={type(response).__name__}')
            return response

        except Exception as e:
            logging.error(f'[SEND] General error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    def __tools_for_request(self) -> list[dict]:
        return [
            {"type": "google_search"},
            {"type": "url_context"},
            {"type": "code_execution"},
        ]

    def __log_google_request(self, label: str, **params) -> None:
        logging.info(
            '[GOOGLE REQUEST] %s params=%s',
            label,
            json.dumps(self.__sanitize_google_log_value(params), ensure_ascii=False, default=str),
        )

    def __sanitize_google_log_value(self, value):
        if value is None or isinstance(value, (bool, int, float)):
            return value

        if isinstance(value, str):
            if len(value) > 4000:
                return {
                    'type': 'str',
                    'length': len(value),
                    'preview': value[:1000],
                }
            return value

        if isinstance(value, bytes):
            return {
                'type': 'bytes',
                'length': len(value),
            }

        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                if key in ('data', 'image_bytes') and isinstance(item, (str, bytes)):
                    sanitized[key] = {
                        'type': type(item).__name__,
                        'length': len(item),
                    }
                else:
                    sanitized[key] = self.__sanitize_google_log_value(item)
            return sanitized

        if isinstance(value, (list, tuple)):
            return [self.__sanitize_google_log_value(item) for item in value]

        model_dump = getattr(value, 'model_dump', None)
        if callable(model_dump):
            return self.__sanitize_google_log_value(model_dump(exclude_none=True))

        return repr(value)

    async def __process_nonstreaming_response(self, response, chat_id: int) -> str | Dict:
        answer, has_grounding = self.__extract_interaction_output(response)
        answer = answer or ""
        
        logging.info(f'[NON-STREAM] Processed response: answer_length={len(answer)}, has_grounding={has_grounding}')
        
        if not answer.strip():
            logging.warning(f'[NON-STREAM] Empty response detected, using fallback message')
            answer = localized_text('empty_response', self.config['bot_language'])
            
        if has_grounding:
            logging.info(f'[NON-STREAM] Adding grounding prefix')
            grounding_prefix = localized_text('web_search_result', self.config['bot_language'])
            answer = f"<i>{grounding_prefix}</i>\n\n{answer}"
        
        self.__store_interaction_id(chat_id, response)
        
        console.print(f"[bold green]✅ [NON-STREAM] Final result:[/bold green] [cyan]length={len(answer)}[/cyan]")

        return answer

    async def __process_streaming_response(self, response, chat_id: int) -> tuple[str | Dict, bool]:
        """
        Process streaming response and yield chunks with final status
        :param response: The streaming response from Google AI
        :param chat_id: Chat ID for history management
        :yield: Tuple of (answer, is_final)
        """
        answer = ''
        has_grounding = False
        chunk_count = 0

        async for chunk in response:
            chunk_count += 1
            event_type = getattr(chunk, 'event_type', '')

            if event_type == 'content.delta':
                delta = getattr(chunk, 'delta', None)
                if getattr(delta, 'type', None) == 'text' and getattr(delta, 'text', None):
                    answer += delta.text
                    logging.info(f'[STREAM] Chunk {chunk_count}: text_length={len(delta.text)}, total_length={len(answer)}')
                    yield answer, False
            elif event_type == 'interaction.complete':
                final_interaction = getattr(chunk, 'interaction', None)
                has_grounding = self.__interaction_used_grounding(final_interaction)
                self.__store_interaction_id(chat_id, final_interaction)
                logging.info(f'[STREAM] Final chunk {chunk_count} received')

        console.print(f"[bold blue]🔄 [STREAM] Streaming completed:[/bold blue] [cyan]chunks={chunk_count}, response_length={len(str(answer))}[/cyan]")
        logging.info(f'[STREAM] Streaming completed: chunks={chunk_count}, response_length={len(str(answer))}')

        # Check if answer is not empty
        if not answer.strip():
            logging.warning(f'[STREAM] Empty response detected')
            answer = localized_text('empty_response', self.config['bot_language'])
        
        if has_grounding:
            logging.info(f'[STREAM] Adding grounding prefix')
            grounding_prefix = localized_text('web_search_result', self.config['bot_language'])
            answer = f"<i>{grounding_prefix}</i>\n\n{answer}"
        
        yield answer, True

    def reset_conversation(self, chat_id: int):
        """Reset the interaction history for a specific chat"""
        if chat_id in self.previous_interactions:
            del self.previous_interactions[chat_id]
        if chat_id in self.last_updated:
            del self.last_updated[chat_id]

    #########################################################
    # Vision
    #########################################################

    async def interpret_image(self, chat_id: int, fileobj, user_name: str | None, prompt=None, use_image_model=False) -> str | Dict:
        # Log vision request
        logging.info(f'[VISION] Vision request: prompt="{prompt}", user_name={user_name}, use_image_model={use_image_model}')

        if use_image_model:
            response = await self.__send_legacy_vision_query(
                fileobj, user_name, prompt, stream=False, use_image_model=True
            )
            return self.__process_legacy_nonstreaming_response(response)

        response = await self.__send_vision_query(chat_id, fileobj, user_name, prompt, stream=False, use_image_model=False)
        return await self.__process_nonstreaming_response(response, chat_id)

    async def interpret_image_stream(self, chat_id: int, fileobj, user_name: str | None, prompt=None, use_image_model=False) -> tuple[str | Dict, bool, bool]:
        # Log streaming vision request
        logging.info(f'[VISION] Starting interpret_image_stream: prompt="{prompt}", user_name={user_name}, use_image_model={use_image_model}')

        if use_image_model:
            response = await self.__send_legacy_vision_query(
                fileobj, user_name, prompt, stream=True, use_image_model=True
            )
            async for answer, is_final in self.__process_legacy_streaming_response(response):
                yield answer, is_final, is_final
            return

        response = await self.__send_vision_query(chat_id, fileobj, user_name, prompt, stream=True, use_image_model=False)

        async for answer, is_final in self.__process_streaming_response(response, chat_id):
            yield answer, is_final, is_final

    @retry(
        reraise=True,
        retry=retry_if_exception_type(Exception),
        wait=wait_fixed(20),
        stop=stop_after_attempt(3)
    )
    async def __send_vision_query(self, chat_id: int, fileobj, user_name: str | None, prompt=None, stream=False, use_image_model=False):
        bot_language = self.config['bot_language']
        try:
            model = self.config['model']
            logging.info(f'[VISION] Vision API request: model={model}, prompt="{prompt}", stream={stream}, use_image_model={use_image_model}')

            # Set default prompt if none provided and no history
            if not prompt or not prompt.strip():
                if chat_id not in self.previous_interactions:
                    # No history - use default vision prompt
                    prompt = self.config['vision_prompt']
            
            speaker = user_name or "User"
            # Add user name if provided
            if user_name and prompt and prompt.strip():
                prompt = f"{user_name}: {prompt}"
            else:
                prompt = f"{speaker} sends a picture"

            # Create config with system instructions
            logging.info(f'[VISION] Creating config with system instructions')
            instructions = MULTIUSER_CHAT_INSTRUCTIONS
            if 'assistant_prompt' in self.config and self.config['assistant_prompt']:
                instructions += self.config['assistant_prompt']
                logging.info(f'[VISION] Added assistant prompt: {self.config["assistant_prompt"]}')

            interaction_input = [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "data": base64.b64encode(fileobj.getvalue()).decode('utf-8'),
                    "mime_type": "image/png",
                },
            ]
            interaction_args = self.__interaction_request_args(
                chat_id=chat_id,
                input_payload=interaction_input,
                system_instruction=instructions,
                tools=[],
                stream=stream,
            )

            self.__log_google_request('interactions.create.vision', **interaction_args)
            response = await self.client.aio.interactions.create(**interaction_args)

            logging.info(f'[VISION] Response received: type={type(response).__name__}')

            return response

        except Exception as e:
            logging.error(f'[VISION] Vision general error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def __send_legacy_vision_query(self, fileobj, user_name: str | None, prompt=None, stream=False, use_image_model=False):
        bot_language = self.config['bot_language']
        try:
            model = self.config.get('image_model', 'gemini-3-pro-image-preview')
            logging.info(f'[VISION] Using image model for editing: {model}')
            logging.info(f'[VISION] Vision API request: model={model}, prompt="{prompt}", stream={stream}, use_image_model={use_image_model}')

            speaker = user_name or "User"
            if user_name and prompt and prompt.strip():
                prompt = f"{user_name}: {prompt}"
            else:
                prompt = f"{speaker} sends a picture"

            parts = [types.Part.from_bytes(
                data=fileobj.getvalue(),
                mime_type='image/png',
            )]
            if prompt and prompt.strip():
                parts.append(types.Part(text=prompt))

            contents = [types.Content(role="user", parts=parts)]
            instructions = MULTIUSER_CHAT_INSTRUCTIONS
            if 'assistant_prompt' in self.config and self.config['assistant_prompt']:
                instructions += self.config['assistant_prompt']

            config = types.GenerateContentConfig(
                system_instruction=instructions,
                response_modalities=["TEXT", "IMAGE"]
            )

            if stream:
                self.__log_google_request(
                    'models.generate_content_stream.legacy_vision',
                    model=model,
                    contents=contents,
                    config=config,
                )
                response = await self.client.aio.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config
                )
            else:
                self.__log_google_request(
                    'models.generate_content.legacy_vision',
                    model=model,
                    contents=contents,
                    config=config,
                )
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config
                )

            logging.info(f'[VISION] Legacy response received: type={type(response).__name__}')
            return response
        except Exception as e:
            logging.error(f'[VISION] Legacy vision error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    def __interaction_request_args(self, chat_id: int, input_payload, system_instruction: str, tools: list, stream: bool) -> dict:
        args = {
            'model': self.config['model'],
            'input': input_payload,
            'system_instruction': system_instruction,
            'generation_config': {
                'temperature': self.config.get('temperature', 1.0),
                'top_p': self.config.get('top_p', 1.0),
                'max_output_tokens': self.config.get('max_output_tokens', 8192),
            },
            'stream': stream,
        }
        if tools:
            args['tools'] = tools
        previous_interaction_id = self.previous_interactions.get(chat_id)
        if previous_interaction_id:
            args['previous_interaction_id'] = previous_interaction_id
        return args

    def __extract_interaction_output(self, interaction) -> tuple[str | Dict, bool]:
        outputs = getattr(interaction, 'outputs', None) or []
        steps = getattr(interaction, 'steps', None) or []
        text_parts = []

        for output in outputs:
            output_type = getattr(output, 'type', '')
            if output_type == 'image' and getattr(output, 'data', None):
                return self.__direct_result_from_base64_image(output.data, getattr(output, 'mime_type', 'image/png')), False
            text_parts.extend(self.__extract_text_parts(output))

        if not text_parts:
            for step in steps:
                text_parts.extend(self.__extract_text_parts(step))

        answer = "\n".join(part for part in text_parts if part).strip()
        if not answer:
            logging.warning(
                '[INTERACTION] No text extracted from interaction: outputs=%s steps=%s',
                self.__describe_interaction_items(outputs),
                self.__describe_interaction_items(steps),
            )
        return answer, self.__interaction_used_grounding(interaction)

    def __extract_text_parts(self, value) -> list[str]:
        if value is None:
            return []

        if isinstance(value, str):
            return [value] if value else []

        if isinstance(value, dict):
            value_type = value.get('type')
            if value_type == 'thought':
                return []
            parts = []
            text = value.get('text')
            if isinstance(text, str) and text:
                parts.append(text)
            for nested_key in ('content', 'contents', 'parts', 'output', 'outputs', 'steps'):
                nested = value.get(nested_key)
                if nested is not None:
                    parts.extend(self.__extract_text_parts(nested))
            return parts

        if isinstance(value, (list, tuple)):
            parts = []
            for item in value:
                parts.extend(self.__extract_text_parts(item))
            return parts

        value_type = getattr(value, 'type', None)
        if value_type == 'thought':
            return []

        parts = []
        text = getattr(value, 'text', None)
        if isinstance(text, str) and text:
            parts.append(text)

        for nested_attr in ('content', 'contents', 'parts', 'output', 'outputs', 'steps'):
            nested = getattr(value, nested_attr, None)
            if nested is not None:
                parts.extend(self.__extract_text_parts(nested))

        return parts

    def __describe_interaction_items(self, items) -> list[dict]:
        descriptions = []
        for item in items:
            descriptions.append({
                'class': type(item).__name__,
                'type': getattr(item, 'type', None),
                'has_text': bool(getattr(item, 'text', None)),
                'attrs': sorted(
                    name for name in ('text', 'content', 'contents', 'parts', 'output', 'outputs', 'steps', 'data')
                    if getattr(item, name, None) is not None
                ),
            })
        return descriptions

    def __interaction_used_grounding(self, interaction) -> bool:
        items = (getattr(interaction, 'outputs', None) or []) + (getattr(interaction, 'steps', None) or [])
        for item in items:
            output_type = getattr(item, 'type', '')
            if 'search' in output_type or 'ground' in output_type or 'url_context' in output_type:
                return True
        return False

    def __store_interaction_id(self, chat_id: int, interaction) -> None:
        interaction_id = getattr(interaction, 'id', None)
        if interaction_id:
            self.previous_interactions[chat_id] = interaction_id

    def __direct_result_from_base64_image(self, encoded_image: str, mime_type: str) -> Dict:
        ext = 'png'
        if 'jpeg' in mime_type or 'jpg' in mime_type:
            ext = 'jpg'
        elif 'webp' in mime_type:
            ext = 'webp'

        filepath = random_file_name(directory_name="uploads/images", extension=ext)
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(encoded_image))

        logging.info(f'[IMAGE] Saved generated image to {filepath}')
        return {
            'direct_result': {
                'kind': 'photo',
                'format': 'path',
                'value': filepath
            }
        }

    def __process_legacy_nonstreaming_response(self, response) -> str | Dict:
        answer = response.text

        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                for part in candidate.content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data is not None:
                        image_bytes = part.inline_data.data
                        mime_type = part.inline_data.mime_type if hasattr(part.inline_data, 'mime_type') else 'image/png'
                        ext = 'png'
                        if 'jpeg' in mime_type or 'jpg' in mime_type:
                            ext = 'jpg'
                        elif 'webp' in mime_type:
                            ext = 'webp'

                        filepath = random_file_name(directory_name="uploads/images", extension=ext)
                        with open(filepath, "wb") as f:
                            f.write(image_bytes)

                        logging.info(f'[IMAGE] Saved generated image to {filepath}')
                        return {
                            'direct_result': {
                                'kind': 'photo',
                                'format': 'path',
                                'value': filepath
                            }
                        }

        return answer or localized_text('empty_response', self.config['bot_language'])

    async def __process_legacy_streaming_response(self, response) -> tuple[str | Dict, bool]:
        answer = ''

        async for chunk in response:
            chunk_text = chunk.text

            if hasattr(chunk, 'candidates') and chunk.candidates:
                candidate = chunk.candidates[0]
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data is not None:
                            image_bytes = part.inline_data.data
                            mime_type = part.inline_data.mime_type if hasattr(part.inline_data, 'mime_type') else 'image/png'
                            ext = 'png'
                            if 'jpeg' in mime_type or 'jpg' in mime_type:
                                ext = 'jpg'
                            elif 'webp' in mime_type:
                                ext = 'webp'

                            filepath = random_file_name(directory_name="uploads/images", extension=ext)
                            with open(filepath, "wb") as f:
                                f.write(image_bytes)

                            logging.info(f'[IMAGE] Saved generated image to {filepath}')
                            yield {
                                'direct_result': {
                                    'kind': 'photo',
                                    'format': 'path',
                                    'value': filepath
                                }
                            }, True
                            return

            if chunk_text:
                answer += chunk_text
                yield answer, False

        yield answer or localized_text('empty_response', self.config['bot_language']), True

    #########################################################
    # Image model
    #########################################################

    async def generate_image(self, prompt: str) -> tuple[str, str]:
        bot_language = self.config['bot_language']
        try:
            image_model = self.config.get('image_model', 'imagen-4.0-generate-001')
            logging.info(f'[IMAGE] Image generation request: prompt="{prompt}", model={image_model}')
            
            # Check if using Gemini image model (starts with "gemini") or Imagen model
            if image_model.startswith('gemini'):
                # Use generate_content with response_modalities for Gemini image models
                logging.info(f'[IMAGE] Using Gemini image model: {image_model}')
                image_config = types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"]
                )
                self.__log_google_request(
                    'models.generate_content.image',
                    model=image_model,
                    contents=[prompt],
                    config=image_config,
                )
                response = await self.client.aio.models.generate_content(
                    model=image_model,
                    contents=[prompt],
                    config=image_config
                )
                
                # Extract image from response
                if not response.candidates or not response.candidates[0].content.parts:
                    logging.error(f'[IMAGE] No response from Gemini: {str(response)}')
                    raise Exception(
                        f"⚠️ _{localized_text('error', bot_language)}._ "
                        f"⚠️\n{localized_text('try_again', bot_language)}."
                    )
                
                # Find image in parts
                image_bytes = None
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data is not None:
                        image_bytes = part.inline_data.data
                        break
                
                if image_bytes is None:
                    logging.error(f'[IMAGE] No image in response: {str(response)}')
                    raise Exception(
                        f"⚠️ _{localized_text('error', bot_language)}._ "
                        f"⚠️\n{localized_text('try_again', bot_language)}."
                    )
                
                # Save image
                temp_filename = random_file_name('uploads/images', 'png')
                with open(temp_filename, 'wb') as f:
                    f.write(image_bytes)
                
                return temp_filename, "1024x1024"
            else:
                # Use generate_images for Imagen models
                logging.info(f'[IMAGE] Using Imagen model: {image_model}')
                image_config = types.GenerateImagesConfig(
                    number_of_images=1,
                )
                self.__log_google_request(
                    'models.generate_images.image',
                    model=image_model,
                    prompt=prompt,
                    config=image_config,
                )
                response = await self.client.aio.models.generate_images(
                    model=image_model,
                    prompt=prompt,
                    config=image_config
                )

                if not response.generated_images or len(response.generated_images) == 0:
                    logging.error(f'[IMAGE] No images generated: {str(response)}')
                    raise Exception(
                        f"⚠️ _{localized_text('error', bot_language)}._ "
                        f"⚠️\n{localized_text('try_again', bot_language)}."
                    )

                # Get the first generated image
                generated_image = response.generated_images[0]
                
                if generated_image.image.image_bytes is not None:
                    image_data = generated_image.image.image_bytes
                    image_data = base64.b64decode(image_data)
                    temp_filename = random_file_name('uploads/images', 'png')
                    
                    with open(temp_filename, 'wb') as f:
                        f.write(image_data)
                    
                    return temp_filename, "1024x1024"
                        
                elif generated_image.image.gcs_uri is not None:
                    return generated_image.image.gcs_uri, "1024x1024"
                    
                else:
                    raise Exception("No image data received from Google AI")
            
        except Exception as e:
            logging.error(f'[IMAGE] Image generation error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    #########################################################
    # TTS/Whisper model
    #########################################################

    async def generate_speech(self, text: str) -> tuple[any, int]:
        """
        Generates an audio from the given text using TTS model.
        :param text: The text to send to the model
        :return: The audio in bytes and the text size
        """
        bot_language = self.config['bot_language']
        try:
            # Log TTS generation request
            logging.info(f'[TTS] TTS generation request: text="{text[:50]}...", model=gemini-2.5-flash-preview-tts')
            tts_prompt = self.config.get(
                'tts_prompt',
                'Speak in French, slowly, with very clear articulation for a beginner traveler'
            ).strip()
            contents = text
            if tts_prompt:
                contents = f'{tts_prompt}: {text}'

            tts_model = self.config.get('tts_model', 'gemini-2.5-flash-preview-tts')
            tts_config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self.config.get('tts_voice', 'Kore'),
                        )
                    )
                ),
            )
            self.__log_google_request(
                'models.generate_content.tts',
                model=tts_model,
                contents=contents,
                config=tts_config,
            )
            
            response = await self.client.aio.models.generate_content(
                model=tts_model,
                contents=contents,
                config=tts_config
            )

            if not response.candidates or len(response.candidates) == 0:
                logging.error(f'[TTS] No response from Google AI: {str(response)}')
                raise Exception(
                    f"⚠️ _{localized_text('error', bot_language)}._ "
                    f"⚠️\n{localized_text('try_again', bot_language)}."
                )

            candidate = response.candidates[0]
            candidate_content = getattr(candidate, 'content', None)
            candidate_parts = getattr(candidate_content, 'parts', None) if candidate_content else None
            if not candidate_parts:
                finish_reason = getattr(candidate, 'finish_reason', 'unknown')
                logging.error(
                    f'[TTS] Gemini returned no audio parts: finish_reason={finish_reason}, candidate={candidate}'
                )
                raise Exception(
                    'Google AI TTS returned no audio data. '
                    'Try a shorter phrase or another voice/model.'
                )

            audio_data = None
            for part in candidate_parts:
                inline_data = getattr(part, 'inline_data', None)
                if inline_data is not None and getattr(inline_data, 'data', None):
                    audio_data = inline_data.data
                    break

            if not audio_data:
                logging.error(f'[TTS] Gemini candidate parts contained no inline audio data: {candidate_parts}')
                raise Exception(
                    'Google AI TTS returned an empty audio payload. '
                    'Try a shorter phrase or another voice/model.'
                )
            
            # Convert PCM to Opus for Telegram compatibility
            # Create temporary WAV file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
                with wave.open(wav_file.name, 'wb') as wf:
                    wf.setnchannels(1)  # Mono
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(24000)  # 24kHz
                    wf.writeframes(audio_data)
                
                # Convert to Opus
                opus_file = wav_file.name.replace('.wav', '.opus')
                subprocess.run([
                    'ffmpeg', '-i', wav_file.name, 
                    '-c:a', 'libopus', '-b:a', '64k',
                    '-y', opus_file
                ], check=True, capture_output=True)
                
                # Read Opus and return as BytesIO
                with open(opus_file, 'rb') as f:
                    opus_data = f.read()
                
                temp_file = io.BytesIO(opus_data)
                temp_file.seek(0)
                
                # Clean up temporary files
                os.unlink(wav_file.name)
                os.unlink(opus_file)
            
            logging.info(f'[TTS] TTS generation completed successfully, audio_size={len(audio_data)}')
            return temp_file, len(text)
            
        except Exception as e:
            logging.error(f'[TTS] TTS generation error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def transcribe(self, filename, prompt=None):
        """
        Transcribes the audio file using Google AI Gemini model.
        """
        bot_language = self.config['bot_language']
        try:
            # Log transcription request
            logging.info(f'[TRANSCRIBE] Transcription request: filename={filename}')
            
            # Read audio file
            with open(filename, 'rb') as f:
                audio_bytes = f.read()
            
            # Determine MIME type based on file extension
            mime_type = 'audio/mp3'  # default
            if filename.endswith('.wav'):
                mime_type = 'audio/wav'
            elif filename.endswith('.ogg'):
                mime_type = 'audio/ogg'
            elif filename.endswith('.opus'):
                mime_type = 'audio/opus'
            
            # Create transcription prompt
            # Use provided prompt, otherwise fall back to global whisper_prompt, then default
            transcription_prompt = "Транскрибуй це аудіо точно. Поверни тільки транскрибований текст без жодних додаткових коментарів. "
            if prompt:
                transcription_prompt += prompt
            elif 'whisper_prompt' in self.config and self.config['whisper_prompt']:
                transcription_prompt += self.config['whisper_prompt']

            transcription_model = self.config.get('transcription_model', 'gemini-2.5-flash')
            transcription_contents = [
                transcription_prompt,
                types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type=mime_type,
                )
            ]
            self.__log_google_request(
                'models.generate_content.transcription',
                model=transcription_model,
                contents=transcription_contents,
            )
            
            # Send transcription request
            response = await self.client.aio.models.generate_content(
                model=transcription_model,
                contents=transcription_contents
            )
            
            if not response.candidates or len(response.candidates) == 0:
                logging.error(f'[TRANSCRIBE] No response from Google AI: {str(response)}')
                raise Exception(
                    f"⚠️ _{localized_text('error', bot_language)}._ "
                    f"⚠️\n{localized_text('try_again', bot_language)}."
                )
            
            # Extract transcribed text
            transcribed_text = response.text
            logging.info(f'[TRANSCRIBE] Transcription completed: text_length={len(transcribed_text)}')
            
            return transcribed_text
            
        except Exception as e:
            logging.error(f'[TRANSCRIBE] Transcription error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e
