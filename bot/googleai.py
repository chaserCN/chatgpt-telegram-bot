from __future__ import annotations
import datetime
import logging
import base64
import json
import traceback

from google import genai
from google.genai import types
import httpx
import io
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from plugin_manager import PluginManager
from utils import is_direct_result, direct_result_kind, localized_text, print_object, random_file_name

# Константи
MULTIUSER_CHAT_INSTRUCTIONS = system_instruction = """
You are a helpful and friendly assistant in a group chat with multiple users.
Users will prefix their messages with their name and a colon (e.g., 'Alice:').
When you respond, be aware of who said what. You can address users by their name if it's natural to do so.
Keep your answers concise and helpful.
"""

class GoogleAIHelper:
    def __init__(self, config: dict, plugin_manager: PluginManager):
        self.client = genai.Client()
        self.config = config
        self.plugin_manager = plugin_manager
        self.last_updated: dict[int: datetime] = {}  
        self.chats: dict[int: any] = {} 

    async def __get_or_create_chat(self, chat_id: int):
        if chat_id not in self.chats:
            system_prompt = MULTIUSER_CHAT_INSTRUCTIONS
            if 'assistant_prompt' in self.config and self.config['assistant_prompt']:
                system_prompt += self.config['assistant_prompt']

            grounding_tool = types.Tool(
                google_search=types.GoogleSearch()
            )

            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[grounding_tool],
                 safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                ]
            )
            self.chats[chat_id] = self.client.aio.chats.create(model=self.config['model'], config=config)
       
        return self.chats[chat_id]

    def __reset_chat_history(self, chat_id: int):
        if chat_id in self.chats:
            del self.chats[chat_id]

    #########################################################
    # Chat model
    #########################################################

    async def get_chat_response(self, chat_id: int, query: str, user_name: str | None) -> Dict | str:
        logging.info(f'[NON-STREAM] Starting get_chat_response: chat_id={chat_id}, query="{query[:50]}..."')
        
        response = await self.__send_query(chat_id, query, user_name, stream=False)    

        # new_response, plugins_used = await self.__handle_function_call(chat_id, response, stream=False)
        # if is_direct_result(new_response):
        #     logging.info(f'Direct result: {direct_result_kind(new_response)}')
        #     return new_response

        answer, has_grounding, _ = self.__process_nonfunction_response(response, check_grounding=True)
        answer = answer or ""
        
        logging.info(f'[NON-STREAM] Processed response: answer_length={len(answer)}, has_grounding={has_grounding}')
        
        if not answer.strip():
            logging.warning(f'[NON-STREAM] Empty response detected, using fallback message')
            answer = localized_text('empty_response', self.config['bot_language'])
            
        if has_grounding:
            logging.info(f'[NON-STREAM] Adding grounding prefix')
            grounding_prefix = localized_text('web_search_result', self.config['bot_language'])
            answer = f"_{grounding_prefix}_\n\n{answer}"
            
        #result = self.__add_plugins_info(result, plugins_used)
        
        logging.info(f'[NON-STREAM] Final result: length={len(answer)}')
        print_object("Result:", answer)

        return answer

    async def get_chat_response_stream(self, chat_id: int, query: str, user_name: str | None) -> tuple[str, bool]:
        logging.info(f'[STREAM] Starting get_chat_response_stream: chat_id={chat_id}, query="{query[:50]}..."')
        
        response = await self.__send_query(chat_id, query, user_name, stream=True)
        
        # response, plugins_used = await self.__handle_function_call(chat_id, response, stream=True)
        # if is_direct_result(response):
        #     yield response, True, True
        #     return

        answer = ''
        has_grounding = False
        chunk_count = 0

        async for chunk in response:
            chunk_count += 1
            print_object("Streaming chunk:", chunk)
            chunk_text, found_grounding, is_final = self.__process_nonfunction_response(chunk, check_grounding=not has_grounding)

            if found_grounding and not has_grounding:
                logging.info(f'[STREAM] Grounding found in chunk {chunk_count}, adding prefix')
                grounding_prefix = localized_text('web_search_result', self.config['bot_language'])
                answer = f"_{grounding_prefix}_\n\n{answer}"

            has_grounding |= found_grounding

            if chunk_text is not None:
                answer += chunk_text
                logging.info(f'[STREAM] Chunk {chunk_count}: text_length={len(chunk_text)}, is_final={is_final}, total_length={len(answer)}')

                if not is_final:
                    yield answer, False
                else:
                    logging.info(f'[STREAM] Final chunk {chunk_count} received')


        logging.info(f'[STREAM] Streaming completed: chunks={chunk_count}, response_length={len(str(answer))}')

        #result = self.__add_plugins_info(answer, plugins_used)
        
        # Check if answer is not empty
        if not answer.strip():
            logging.warning(f'[STREAM] Empty response detected, trying non-streaming retry')
            answer = localized_text('empty_response', self.config['bot_language'])
            # Try one more time with non-streaming response
            try:
                logging.info(f'[STREAM] Attempting non-streaming retry')
                retry_response = await self.get_chat_response(chat_id, query, user_name)
                if retry_response and retry_response.strip():
                    logging.info(f'[STREAM] Retry successful: length={len(retry_response)}')
                    answer = retry_response
                else:
                    logging.warning(f'[STREAM] Retry also returned empty response')
            except Exception as e:
                logging.error(f'[STREAM] Retry failed: {str(e)}')
        else:
            logging.info(f'[STREAM] Final answer ready: length={len(answer)}')
        
        yield answer, True

    @retry(
        reraise=True,
        retry=retry_if_exception_type(Exception),
        wait=wait_fixed(20),
        stop=stop_after_attempt(3)
    )
    async def __send_query(self, chat_id: int, query: str, user_name: str | None, stream=False):
        bot_language = self.config['bot_language']
        try:
            if self.__max_age_reached(chat_id):
                self.__reset_chat_history(chat_id)

            self.last_updated[chat_id] = datetime.datetime.now()

            if user_name:
                query = f"{user_name}: {query}"

            # Log the API request
            logging.info(f'API request: model={self.config["model"]}, query="{query}", stream={stream}')

            # Get or create chat session
            chat = await self.__get_or_create_chat(chat_id)
            
            # Send message using chat API
            if stream:
                response = await chat.send_message_stream(query)
            else:
                response = await chat.send_message(query)

            print_object("Response:", response)

            return response

        except Exception as e:
            logging.error(f'General error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def __handle_function_call(self, chat_id, response, stream=False, times=0, plugins_used=()):
        logging.info(f"__handle_function_call: chat_id={chat_id}, stream={stream}, times={times}, plugins_used={plugins_used}")
        
        # Log function call handling
        logging.info(f'Function call handling: stream={stream}, times={times}, plugins_used={plugins_used}')
        
        # Google AI doesn't have built-in function calling like OpenAI, so we'll implement a basic version
        # For now, we'll just return the response as-is
        return response, plugins_used

    async def __execute_function_calls(self, chat_id, function_calls, stream, times, plugins_used, response_id):
        # Google AI doesn't have built-in function calling, so this is a placeholder
        logging.info(f"__execute_function_calls: chat_id={chat_id}, function_calls_count={len(function_calls)}, stream={stream}, times={times}, response_id={response_id}")
        return response_id, plugins_used

    def __process_nonfunction_response(self, response, check_grounding: bool) -> tuple[str | None, bool, bool]:
        answer = response.text
        
        has_grounding = False
        is_final = False
        
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            
            if check_grounding:
                has_grounding = (hasattr(candidate, 'grounding_metadata') and 
                               candidate.grounding_metadata and 
                               hasattr(candidate.grounding_metadata, 'web_search_queries') and 
                               candidate.grounding_metadata.web_search_queries is not None)
                if has_grounding:
                    logging.info(f'[PROCESS] Grounding metadata found: {candidate.grounding_metadata.web_search_queries}')
            
            if hasattr(candidate, 'finish_reason') and candidate.finish_reason == 'STOP':
                is_final = True
                logging.info(f'[PROCESS] Final chunk detected (STOP)')

        logging.info(f'[PROCESS] Processed: text_length={len(answer) if answer else 0}, has_grounding={has_grounding}, is_final={is_final}')
        return answer, has_grounding, is_final

    def __add_plugins_info(self, result, plugins_used):
        if isinstance(result, str) and len(plugins_used) > 0 and self.config['show_plugins_used']:
            plugin_names = tuple(
                self.plugin_manager.get_plugin_source_name_with_icon(plugin) for plugin in plugins_used)
            result += f"\n\n---\n{', '.join(plugin_names)}"
        
        return result

    def __tools_for_request(self) -> Dict:
        # Google AI doesn't have tools like OpenAI, so return empty list
        return []

    def __max_age_reached(self, chat_id) -> bool:
        if chat_id not in self.last_updated:
            return True
        age = datetime.datetime.now() - self.last_updated[chat_id]
        max_age_reached = age.total_seconds() > self.config['max_conversation_age_minutes'] * 60
        
        # Clear chat session if max age is reached
        if max_age_reached and chat_id in self.chats:
            del self.chats[chat_id]
        
        return max_age_reached

    def reset_conversation(self, chat_id: int):
        """Reset the conversation history for a specific chat"""
        if chat_id in self.chats:
            del self.chats[chat_id]
        if chat_id in self.last_updated:
            del self.last_updated[chat_id]

    #########################################################
    # Vision
    #########################################################

    async def interpret_image(self, chat_id: int, fileobj, user_name: str | None, prompt=None) -> str | Dict:
        # Log vision request
        logging.info(f'Vision request: prompt="{prompt}", user_name={user_name}')
        
        response = await self.__send_vision_query(chat_id, fileobj, user_name, prompt)

        answer = await self.__process_nonfunction_response(response)
        self.last_response_ids[chat_id] = str(id(response))
        
        # Log vision response
        logging.info(f'Vision response: response_length={len(answer)}')

        return answer

    async def interpret_image_stream(self, chat_id: int, fileobj, user_name: str | None, prompt=None) -> tuple[str, bool, bool]:
        # Log streaming vision request
        logging.info(f'Streaming vision request: prompt="{prompt}", user_name={user_name}')
        
        response = await self.__send_vision_query(chat_id, fileobj, user_name, prompt, stream=True)

        answer = ''

        for chunk in response:
            # Log streaming vision events with full chunk data
            chunk_data = {
                'has_text': hasattr(chunk, 'text'),
                'text_length': len(chunk.text) if hasattr(chunk, 'text') else 0,
                'full_chunk': str(chunk),
                'chunk_type': type(chunk).__name__,
                'chunk_attributes': {attr: getattr(chunk, attr, None) for attr in dir(chunk) if not attr.startswith('_')}
            }
            logging.info(f'Vision streaming chunk: {chunk_data}')
            
            if hasattr(chunk, 'text') and chunk.text:
                answer += chunk.text
                yield answer, False, False

        # Log completed streaming vision response
        logging.info(f'Vision streaming completed: response_length={len(str(answer))}')
        
        yield answer, True, True

    @retry(
        reraise=True,
        retry=retry_if_exception_type(Exception),
        wait=wait_fixed(20),
        stop=stop_after_attempt(3)
    )
    async def __send_vision_query(self, chat_id: int, fileobj, user_name: str | None, prompt=None, stream=False):
        bot_language = self.config['bot_language']
        try:
            if self.__max_age_reached(chat_id):
                self.last_response_ids[chat_id] = None
            self.last_updated[chat_id] = datetime.datetime.now()

            prompt = self.config['vision_prompt'] if prompt is None else prompt
            if user_name:
                prompt = f"{user_name} says: {prompt}"

            # Log vision API request
            logging.info(f'Vision API request: model={self.config.get("vision_model", self.config["model"])}, prompt="{prompt}", stream={stream}')

            # Create image part
            image_part = {
                "mime_type": "image/png",
                "data": fileobj.getvalue()
            }

            # Send vision request using new API
            if stream:
                response = self.client.models.generate_content_stream(
                    model=self.config.get('vision_model', self.config['model']),
                    contents=[prompt, image_part]
                )
            else:
                response = self.client.models.generate_content(
                    model=self.config.get('vision_model', self.config['model']),
                    contents=[prompt, image_part]
                )

            # Log vision API response with full response data
            response_data = {
                'response_id': str(id(response)),
                'response_type': 'success',
                'response_object': str(response),
                'response_type_name': type(response).__name__,
                'response_attributes': {attr: getattr(response, attr, None) for attr in dir(response) if not attr.startswith('_')}
            }
            logging.info(f'Vision API response: {response_data}')
            
            return response

        except Exception as e:
            logging.error(f'Vision general error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    #########################################################
    # Image model
    #########################################################

    async def generate_image(self, prompt: str) -> tuple[str, str]:
        bot_language = self.config['bot_language']
        try:
            # Log image generation request
            logging.info(f'Image generation request: prompt="{prompt}", model=gemini-pro-vision')
            
            # Google AI doesn't have a dedicated image generation API like DALL-E
            # This is a placeholder - you might need to use a different service
            raise Exception("Image generation is not supported with Google AI. Please use a different service like DALL-E or Stable Diffusion.")
            
        except Exception as e:
            logging.error(f'Image generation error: {str(e)}')
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
            # Google AI doesn't have built-in TTS like OpenAI
            # This is a placeholder - you might need to use a different service
            raise Exception("Text-to-speech is not supported with Google AI. Please use a different service like OpenAI TTS or Google Cloud TTS.")
            
        except Exception as e:
            logging.error(f'TTS generation error: {str(e)}')
            raise Exception(f"⚠️ _{localized_text('error', bot_language)}._ ⚠️\n{str(e)}") from e

    async def transcribe(self, filename):
        """
        Transcribes the audio file using the Whisper model.
        """
        try:
            # Google AI doesn't have built-in transcription like OpenAI
            # This is a placeholder - you might need to use a different service
            raise Exception("Audio transcription is not supported with Google AI. Please use a different service like OpenAI Whisper or Google Cloud Speech-to-Text.")
            
        except Exception as e:
            logging.exception(e)
            raise Exception(f"⚠️ _{localized_text('error', self.config['bot_language'])}._ ⚠️\n{str(e)}") from e

