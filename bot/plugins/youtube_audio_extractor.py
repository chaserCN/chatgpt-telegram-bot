import logging
import os
import re
from typing import Dict
import yt_dlp

from .plugin import Plugin


class YouTubeAudioExtractorPlugin(Plugin):
    """
    A plugin to extract audio from a YouTube video
    """

    def get_source_name(self) -> str:
        return "YouTube Audio Extractor"

    def get_spec(self) -> [Dict]:
        return [{
            "type": "function",
            "name": "extract_youtube_audio",
            "description": "Extract audio from a YouTube video",
            "parameters": {
                "type": "object",
                "properties": {
                    "youtube_link": {"type": "string", "description": "YouTube video link to extract audio from"}
                },
                "required": ["youtube_link"],
            },
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        link = kwargs['youtube_link']
        try:
            # Get video info first - use default settings that work
            info_opts = {
                'quiet': True,
            }
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(link, download=False)
                title = info.get('title', 'Unknown')
            
            # Create uploads/youtube directory and filename
            uploads_dir = 'uploads/youtube'
            if not os.path.exists(uploads_dir):
                os.makedirs(uploads_dir)
                
            # Create filename from video title (will be m4a)
            clean_title = re.sub(r'[^\w\-_\. ]', '_', title)
            base_filename = os.path.join(uploads_dir, clean_title)
            
            # Remove any existing files with the same base name (different extensions)
            for ext in ['.mp3', '.m4a', '.opus', '.webm', '.wav', '.flac']:
                existing_file = base_filename + ext
                if os.path.exists(existing_file):
                    os.remove(existing_file)
            
            # Set output template (yt-dlp will convert to .mp3)
            output_path = base_filename + '.%(ext)s'
            
            # Configure yt-dlp options - use postprocessor with simple settings (no anti-bot)
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'outtmpl': output_path,  # Template already has .%(ext)s
                'noplaylist': True,
                'quiet': True,
            }
            
            # Download and extract audio
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([link])
            
            # Find the actual created file (should be .mp3 after conversion)
            actual_file = None
            for ext in ['.mp3', '.m4a', '.opus', '.webm']:
                potential_path = base_filename + ext
                if os.path.exists(potential_path):
                    actual_file = potential_path
                    break
            
            if actual_file:
                return {
                    'direct_result': {
                        'kind': 'file',
                        'format': 'path',
                        'value': actual_file
                    }
                }
            else:
                return {'result': 'Failed to find downloaded audio file'}
        except Exception as e:
            logging.warning(f'Failed to extract audio from YouTube video: {str(e)}')
            return {'result': 'Failed to extract audio'}
