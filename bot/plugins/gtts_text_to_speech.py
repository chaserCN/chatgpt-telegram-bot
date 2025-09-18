import datetime
import os
from typing import Dict

from gtts import gTTS

from .plugin import Plugin


class GTTSTextToSpeech(Plugin):
    """
    A plugin to convert text to speech using Google Translate's Text to Speech API
    """

    def get_source_name(self) -> str:
        return "gTTS"

    def get_spec(self) -> [Dict]:
        return [{
            "type": "function",
            "name": "google_translate_text_to_speech",
            "description": "Translate text to speech using Google Translate's Text to Speech API",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to translate to speech"},
                    "lang": {
                        "type": "string", "description": "The language of the text to translate to speech."
                                                         "Infer this from the language of the text.",
                    },
                },
                "required": ["text", "lang"],
            },
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        # Create uploads/tts directory and filename
        uploads_dir = 'uploads/tts'
        if not os.path.exists(uploads_dir):
            os.makedirs(uploads_dir)
            
        tts = gTTS(kwargs['text'], lang=kwargs.get('lang', 'en'))
        filename = f'gtts_{datetime.datetime.now().timestamp()}.mp3'
        output_path = os.path.join(uploads_dir, filename)
        tts.save(output_path)
        
        return {
            'direct_result': {
                'kind': 'file',
                'format': 'path',
                'value': output_path
            }
        }
