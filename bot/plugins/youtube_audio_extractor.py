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
            # Check for cookies from environment variable
            youtube_cookies_content = os.environ.get('YOUTUBE_COOKIES_CONTENT', '')
            
            # Create cookies file from env variable if needed
            cookies_file_path = None
            if youtube_cookies_content:
                # Create uploads directory if it doesn't exist
                uploads_dir = 'uploads'
                if not os.path.exists(uploads_dir):
                    os.makedirs(uploads_dir)
                
                # Check if cookies file already exists
                cookies_file_path = os.path.join(uploads_dir, 'youtube_cookies.txt')
                if not os.path.exists(cookies_file_path):
                    # Save cookies to file (replace \n with actual newlines)
                    with open(cookies_file_path, 'w') as f:
                        # Replace \n with actual newlines
                        cookies_content = youtube_cookies_content.replace('\\n', '\n')
                        f.write(cookies_content)
            else:
                logging.info('No cookies configured, using anonymous mode')
            
            # Get video info first
            info_opts = {
                'quiet': True,
            }
            
            # Apply cookies settings
            if cookies_file_path:
                info_opts['cookiefile'] = cookies_file_path
                
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
            
            # Configure yt-dlp options with cookies if available
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
            
            # Apply cookies settings for download
            if cookies_file_path:
                ydl_opts['cookiefile'] = cookies_file_path
            else:
                # Try minimal anti-bot headers as fallback
                ydl_opts['http_headers'] = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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
                result = {
                    'direct_result': {
                        'kind': 'file',
                        'format': 'path',
                        'value': actual_file
                    }
                }
            else:
                result = {'result': 'Failed to find downloaded audio file'}
                    
            return result
        except Exception as e:                    
            logging.warning(f'Failed to extract audio from YouTube video: {str(e)}')
            return {'result': 'Failed to extract audio'}
