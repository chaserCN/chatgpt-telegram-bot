#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Скрипт для загрузки аудио с YouTube, транскрипции и форматирования текста
с поддержкой нескольких AI-провайдеров (OpenAI, Google).

Этот скрипт выполняет следующие шаги:
1. Загружает аудиодорожку из видео на YouTube.
2. Транскрибирует аудио в текст с помощью OpenAI Whisper.
3. Форматирует и исправляет транскрипцию с помощью GPT модели, сохраняя весь контент.
4. Разбивает итоговый текст на фрагменты (чанки) не более 4096 символов, 
   сохраняя целостность предложений.
5. Сохраняет результат в виде текстовых файлов.

Требования:
- Python 3.7+
- Зависимости: openai, python-dotenv, yt-dlp, nltk
  (pip install openai python-dotenv yt-dlp nltk)
- .env файл с переменными OPENAI_API_KEY и OPENAI_MODEL.

Пример .env файла:
OPENAI_API_KEY="sk-..."
OPENAI_MODEL="gpt-4o" 

Запуск:
python transcribe_youtube.py "YOUTUBE_URL" --provider [openai|google]
"""

import os
import sys
import asyncio
import yt_dlp
from dotenv import load_dotenv
import logging
import re
import argparse
from abc import ABC, abstractmethod

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# Отключаем слишком подробные логи от HTTP-клиента OpenAI
logging.getLogger("httpx").setLevel(logging.WARNING)


# -----------------------------------------------------------------------------
# AI Providers
# -----------------------------------------------------------------------------

# Импорты для провайдеров
import openai
from google import genai
from google.genai import types

class AIProvider(ABC):
    """Абстрактный базовый класс для AI провайдеров."""
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(f"API key for {self.__class__.__name__} is missing.")
        self.api_key = api_key

    @abstractmethod
    async def transcribe(self, audio_path: str) -> str:
        """Транскрибирует аудиофайл."""
        pass

    @abstractmethod
    async def format(self, raw_text: str) -> str:
        """Форматирует сырой текст транскрипции."""
        pass

    @abstractmethod
    async def verify(self, raw_text: str, formatted_text: str) -> bool:
        """Проверяет, что при форматировании не была утеряна информация."""
        pass

# --- Реализация для OpenAI ---

class OpenAIProvider(AIProvider):
    """Реализация AI-провайдера для OpenAI."""
    def __init__(self, api_key: str):
        super().__init__(api_key)
        if not openai:
            raise ImportError("Библиотека OpenAI не установлена. Пожалуйста, выполните: pip install openai")
        self.client = openai.AsyncOpenAI(api_key=self.api_key)
        self.model = "gpt-4o"

    async def transcribe(self, audio_path: str) -> str:
        logging.info(f"Транскрипция через OpenAI Whisper")
        with open(audio_path, "rb") as audio_file:
            transcript = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )
        return transcript

    async def format(self, raw_text: str) -> str:
        logging.info(f"Форматирование через OpenAI {self.model}...")
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": FORMAT_SYSTEM_PROMPT},
                {"role": "user", "content": raw_text}
            ]
        )
        return response.choices[0].message.content

    async def verify(self, raw_text: str, formatted_text: str) -> bool:
        logging.info(f"Верификация через OpenAI {self.model}...")
        user_content = (
            f"--- ИСХОДНЫЙ ТЕКСТ ---\n{raw_text}\n\n"
            f"--- ОТРЕДАКТИРОВАННЫЙ ТЕКСТ ---\n{formatted_text}"
        )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            top_p=1e-9,
        )
        result = response.choices[0].message.content.strip()
        
        verification_passed = "ПОДТВЕРЖДЕНО" in result
        if verification_passed:
            logging.info("✅ Верификация пройдена: GPT подтвердил, что потерь информации нет.")
        else:
            logging.warning(f"🚨 ВНИМАНИЕ! Верификация НЕ пройдена! Ответ GPT: '{result}'")
            
        return verification_passed

# --- Реализация для Google ---

class GoogleProvider(AIProvider):
    """Реализация AI провайдера для Google Gemini."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Необходимо установить GEMINI_API_KEY")
        
        self.api_key = api_key
        # Новый SDK использует клиент для конфигурации
        self.client = genai.Client(api_key=self.api_key)
        self.model = "models/gemini-1.5-flash"
        # Gemini API может быть чувствителен к контенту, снижаем порог блокировки
        # self.safety_settings = {
        #     HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        #     HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        #     HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        #     HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        # }

    async def transcribe(self, audio_path: str) -> str:
        logging.info(f"Транскрипция через Google Gemini")
        
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=[
                "Распознай эту речь. Выведи только текст.",
                types.Part.from_bytes(
                    mime_type="audio/mpeg", # yt-dlp сохраняет в mp3, что соответствует mpeg
                    data=audio_bytes
                )
            ]
        )
        return response.text


    async def format(self, raw_text: str) -> str:
        logging.info(f"Форматирование через Google {self.model}...")
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=[FORMAT_SYSTEM_PROMPT, raw_text]
        )
        return response.text

    async def verify(self, raw_text: str, formatted_text: str) -> bool:
        logging.info(f"Верификация через Google {self.model}...")
        
        verification_prompt = f"ИСХОДНЫЙ ТЕКСТ:\n```\n{raw_text}\n```\n\nОТРЕДАКТИРОВАННЫЙ ТЕКСТ:\n```\n{formatted_text}\n```"
        
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=[VERIFY_SYSTEM_PROMPT, verification_prompt]
            # safety_settings=self.safety_settings
        )
        
        verification_text = response.text.strip().upper()
        
        verification_passed = "ПОДТВЕРЖДЕНО" in verification_text
        if verification_passed:
            logging.info("✅ Верификация пройдена: Gemini подтвердил, что потерь информации нет.")
        else:
            logging.warning(f"🚨 ВНИМАНИЕ! Верификация НЕ пройдена! Ответ Gemini: '{verification_text}'")
            
        return verification_passed


# --- Глобальные константы для промптов ---

FORMAT_SYSTEM_PROMPT = (
    "Ты — высокоточный ассистент-форматировщик. Твоя задача — взять сырой текст транскрипции и сделать его читабельным. "
    "Твои единственные разрешенные действия: "
    "1. Расставлять знаки препинания (точки, запятые). "
    "2. Исправлять явные орфографические ошибки и опечатки. "
    "3. Разделять сплошной текст на абзацы, где это логически уместно. "
    "ЗАПРЕЩЕНО: "
    "- ИЗМЕНЯТЬ СЛОВА И ФАКТЫ, даже если они кажутся тебе неверными. "
    "- ДОБАВЛЯТЬ ЛЮБУЮ ИНФОРМАЦИЮ, которой не было в исходном тексте (никаких примеров, уточнений, комментариев). "
    "- УДАЛЯТЬ или сокращать что-либо. "
    "Твоя цель — сохранить 100% исходной информации, лишь улучшив её читаемость. Верни только отформатированный текст."
)

VERIFY_SYSTEM_PROMPT = (
    "Ты — ассистент по контролю качества. Тебе предоставлены два текста: 'ИСХОДНЫЙ ТЕКСТ' и 'ОТРЕДАКТИРОВАННЫЙ ТЕКСТ'.\n"
    "Твоя задача — внимательно сравнить их и подтвердить, что в 'ОТРЕДАКТИРОВАННОМ ТЕКСТЕ' не была утеряна или искажена никакая фактическая информация из 'ИСХОДНОГО ТЕКСТА'. "
    "Исправления орфографии, пунктуации и разбиение на абзацы — это допустимые изменения.\n"
    "Удаление слов, целых предложений или изменение смысла — недопустимо.\n"
    "Ответь ОДНИМ СЛОВОМ: 'ПОДТВЕРЖДЕНО', если вся информация сохранена, или 'ОШИБКА' и кратко укажи, что было утеряно, если найдешь расхождения."
)

# --- Основные функции (не зависят от провайдера) ---

def download_audio(youtube_url: str, output_path: str = "transcription_output") -> str:
    """
    Загружает аудио с YouTube в формате MP3.
    """
    logging.info(f"Загрузка аудио с URL: {youtube_url}")
    
    if not os.path.exists(output_path):
        os.makedirs(output_path)

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            # yt-dlp может менять некоторые символы в имени файла, нужно получить реальное имя
            filename = ydl.prepare_filename(info)
            # После постпроцессинга расширение может быть неверным, исправляем
            base, _ = os.path.splitext(filename)
            mp3_filename = base + '.mp3'
            
            if os.path.exists(mp3_filename):
                logging.info(f"Аудио успешно сохранено: {mp3_filename}")
                return mp3_filename
            else:
                 # Если файл не найден, возможно, он имеет другое расширение
                logging.warning(f"Ожидаемый mp3 файл не найден: {mp3_filename}")
                # Пробуем найти любой аудиофайл в папке
                for file in os.listdir(output_path):
                    if file.startswith(os.path.basename(base)):
                        logging.info(f"Найден аудиофайл: {os.path.join(output_path, file)}")
                        return os.path.join(output_path, file)
                raise FileNotFoundError("Не удалось найти скачанный аудиофайл.")
                
    except Exception as e:
        logging.error(f"Ошибка при загрузке аудио: {e}")
        raise

def split_text_into_chunks(text: str, max_chars: int = 4096) -> list:
    """
    Разбивает отформатированный текст на чанки, не превышающие max_chars, 
    сохраняя абзацы (разбиение по '\n').
    """
    logging.info(f"Разбиение отформатированного текста на чанки по {max_chars} символов...")
    
    # Разбиваем текст на параграфы по переносу строки
    paragraphs = text.split('\n')
    
    chunks = []
    current_chunk = ""
    
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
            
        # Если сам абзац больше лимита, его нужно разбить принудительно
        if len(paragraph) > max_chars:
            # Сначала сохраняем то, что уже накоплено в чанке
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            
            # Разбиваем гигантский абзац на части
            for i in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[i:i+max_chars])
            continue

        # Проверяем, не превысит ли добавление нового параграфа лимит
        if len(current_chunk) + len(paragraph) + 2 > max_chars: # +2 для учета разделителя "\n\n"
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = paragraph
        else:
            if current_chunk:
                current_chunk += "\n\n" + paragraph
            else:
                current_chunk = paragraph
            
    # Добавляем последний оставшийся чанк
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    logging.info(f"Текст разбит на {len(chunks)} чанков.")
    return chunks

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
async def main():
    # 1. Парсинг аргументов
    parser = argparse.ArgumentParser(
        description="""Скрипт для загрузки аудио с YouTube, транскрипции и форматирования текста.
        
Поддерживает двух AI-провайдеров: OpenAI и Google Gemini.""",
        epilog="""Примеры использования:
  
  # Использование OpenAI (по умолчанию):
  python transcribe_youtube.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  
  # Явное указание OpenAI:
  python transcribe_youtube.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --provider openai
  
  # Использование Google Gemini:
  python transcribe_youtube.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --provider google
""",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("url", type=str, help="URL видео на YouTube.")
    parser.add_argument("--provider", type=str, choices=["openai", "google"], default="openai", help="AI провайдер для транскрипции и форматирования (по умолчанию: openai).")
    args = parser.parse_args()

    # 2. Инициализация провайдера
    provider: AIProvider = None
    try:
        if args.provider == "google":
            load_dotenv('.env_google')
            api_key = os.getenv("GEMINI_API_KEY")
            provider = GoogleProvider(api_key)
            logging.info("Используется провайдер: Google Gemini")
        else: # openai по умолчанию
            load_dotenv()
            api_key = os.getenv("OPENAI_API_KEY")
            provider = OpenAIProvider(api_key)
            logging.info("Используется провайдер: OpenAI")
    except (ValueError, ImportError) as e:
        logging.error(f"Ошибка инициализации провайдера: {e}")
        sys.exit(1)

    audio_path = None
    try:
        # 3. Загрузка аудио
        audio_path = download_audio(args.url)

        # 4. Транскрипция
        raw_transcript = await provider.transcribe(audio_path)
        
        # 4.5 Сохранение сырой транскрипции
        safe_base_filename = re.sub(r'[^\w\-_\. ]', '_', os.path.splitext(os.path.basename(audio_path))[0])
        raw_transcript_path = os.path.join("transcription_output", f"{safe_base_filename}_raw.txt")
        with open(raw_transcript_path, "w", encoding="utf-8") as f:
            f.write(raw_transcript)
        # logging.info(f"Сырая транскрипция сохранена в: {raw_transcript_path}")

        # 5. Форматирование
        formatted_transcript = await provider.format(raw_transcript)
        
        # 5.5. Верификация
        await provider.verify(raw_transcript, formatted_transcript)
        # if not verification_passed:
        #     logging.warning("Верификация НЕ пройдена. Результат может быть неточным.")

        # 6. Разбиение на чанки
        chunks = split_text_into_chunks(formatted_transcript)
        
        # 7. Сохранение результатов
        safe_base_filename = re.sub(r'[^\w\-_\. ]', '_', os.path.splitext(os.path.basename(audio_path))[0])
        
        output_files = []

        # Сохраняем сырую транскрипцию
        raw_transcript_path = os.path.join("transcription_output", f"{safe_base_filename}_raw.txt")
        with open(raw_transcript_path, "w", encoding="utf-8") as f:
            f.write(raw_transcript)
        output_files.append(raw_transcript_path)

        # Сохраняем полную форматированную транскрипцию
        formatted_transcript_path = os.path.join("transcription_output", f"{safe_base_filename}_formatted.txt")
        with open(formatted_transcript_path, "w", encoding="utf-8") as f:
            f.write(formatted_transcript)
        output_files.append(formatted_transcript_path)

        # Сохраняем все чанки в один файл с разделителем
        if chunks:
            chunks_path = os.path.join("transcription_output", f"{safe_base_filename}_chunks.txt")
            with open(chunks_path, "w", encoding="utf-8") as f:
                for i, chunk in enumerate(chunks, 1):
                    f.write(f"--- CHUNK {i} ---\n\n")
                    f.write(chunk)
                    if i < len(chunks):
                        f.write("\n\n\n")
            output_files.append(chunks_path)

        logging.info("🎉 Все операции успешно завершены!")
        
        # Выводим список созданных файлов
        logging.info("--- Результаты ---")
        for file_path in output_files:
            logging.info(f"-> {file_path}")
        logging.info("------------------")

    except Exception as e:
        logging.error(f"Произошла критическая ошибка: {e}")
    finally:
        # 8. Очистка
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
            logging.info(f"Временный аудиофайл удален: {audio_path}")


if __name__ == "__main__":
    asyncio.run(main())
