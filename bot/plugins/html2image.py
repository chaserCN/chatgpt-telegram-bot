import os, random, string
import imgkit
from typing import Dict
from .plugin import Plugin, generate_random_string


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
        image_file_path = self.tmp_file_path()
        imgkit.from_string(kwargs['inputHTML'], image_file_path)

        return {
            'direct_result': {
                'kind': 'photo',
                'format': 'path',
                'value': image_file_path
            }
        }

    def tmp_file_path(self):
        if not os.path.exists("uploads/html"):
            os.makedirs("uploads/html")

        return os.path.join("uploads/html", f"{generate_random_string(15)}.png")
