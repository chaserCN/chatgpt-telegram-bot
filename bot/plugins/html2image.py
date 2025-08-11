import os, random, string
import imgkit
from typing import Dict
from .plugin import Plugin
from utils import random_file_name

class Html2ImagePlugin(Plugin):
    """
    A plugin to answer questions using WolframAlpha.
    """

    def get_source_name(self) -> str:
        return "Html2Image"

    def get_icon(self) -> str:
        return "🖌️"

    def get_spec(self) -> [Dict]:
        return [{
            "type": "function",
            "name": "transform_html_to_image",
            "description": "Transform html to an image. Input should be a valid html",
            "parameters": {
                "type": "object",
                "properties": {
                    "inputHTML": {"type": "string", "description": "A string in HTML format"}
                },
                "required": ["inputHTML"]
            }
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        image_file_path = random_file_name("uploads/html", "png")
        imgkit.from_string(kwargs['inputHTML'], image_file_path)

        return {
            'direct_result': {
                'kind': 'photo',
                'format': 'path',
                'value': image_file_path
            }
        }
