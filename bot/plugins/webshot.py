import os, requests, random, string
from typing import Dict

from utils import random_file_name
from .plugin import Plugin


class WebshotPlugin(Plugin):
    """
    A plugin to screenshot a website
    """
    def get_source_name(self) -> str:
        return "WebShot"

    def get_spec(self) -> [Dict]:
        return [{
            "type": "function",
            "name": "screenshot_website",
            "description": "Show screenshot/image of a website from a given url or domain name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Website url or domain name. Correctly formatted url is required. Example: https://www.google.com"}
                },
                "required": ["url"],
            },
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        try:
            image_url = f'https://image.thum.io/get/maxAge/12/width/720/{kwargs["url"]}'
            
            # preload url first
            requests.get(image_url)

            # download the actual image
            response = requests.get(image_url, timeout=30)

            if response.status_code == 200:
                image_file_path = random_file_name("uploads/webshot", "png")
                with open(image_file_path, "wb") as f:
                    f.write(response.content)

                return {
                    'direct_result': {
                        'kind': 'photo',
                        'format': 'path',
                        'value': image_file_path
                    }
                }
            else:
                return {'result': 'Unable to screenshot website'}
        except:
            if 'image_file_path' in locals():
                os.remove(image_file_path)
                
            return {'result': 'Unable to screenshot website'}
