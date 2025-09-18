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
            # Create audio_downloads directory if it doesn't exist
            audio_downloads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio_downloads')
            if not os.path.exists(audio_downloads_dir):
                os.makedirs(audio_downloads_dir)
            
            # Get video info first to extract title
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(link, download=False)
                title = info.get('title', 'Unknown')
            
            # Create filename from video title
            filename = re.sub(r'[^\w\-_\. ]', '_', title) + '.mp3'
            output_path = os.path.join(audio_downloads_dir, filename)
            
            # Remove any existing files with the same base name (different extensions)
            base_name = output_path.replace('.mp3', '')
            for ext in ['.mp3', '.m4a', '.opus', '.webm', '.wav', '.flac']:
                existing_file = base_name + ext
                if os.path.exists(existing_file):
                    os.remove(existing_file)
            
            # Configure yt-dlp options for audio extraction
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'outtmpl': output_path.replace('.mp3', '.%(ext)s'),  # yt-dlp will add correct extension
                'noplaylist': True,
                'quiet': True,
            }
            
            # Download and extract audio
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([link])
            
            # Check if file exists (yt-dlp might create it with slightly different name)
            if not os.path.exists(output_path):
                # Try to find the actual created file
                base_name = output_path.replace('.mp3', '')
                for ext in ['.mp3', '.m4a', '.opus', '.webm']:
                    potential_path = base_name + ext
                    if os.path.exists(potential_path):
                        output_path = potential_path
                        break
            
            return {
                'direct_result': {
                    'kind': 'file',
                    'format': 'path',
                    'value': output_path
                }
            }
        except Exception as e:
            logging.warning(f'Failed to extract audio from YouTube video: {str(e)}')
            return {'result': 'Failed to extract audio'}
