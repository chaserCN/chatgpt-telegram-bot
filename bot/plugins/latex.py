import os, random, string
import urllib
import urllib.parse
import urllib.request
from typing import Dict
from urllib.error import HTTPError
from cairosvg import svg2png

from .plugin import Plugin

class LatexConverterPlugin(Plugin):
    """
    A plugin to answer questions using WolframAlpha.
    """

    def get_source_name(self) -> str:
        return "LatexConverter"

    def get_spec(self) -> [Dict]:
        return [{
            "name": "transform_latex_to_image",
            "description": "Transform latex formula to an image. Input should be a valid LaTeX expression",
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "A string in valid LaTeX format"}
                },
                "required": ["from"]
            }
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        data = {
            'from': kwargs['from'],
        }

        query = urllib.parse.urlencode(data)
        url = 'https://math.vercel.app/?' + query

        try:
            resp = urllib.request.urlopen(url)
            body = resp.read()

            if not os.path.exists("uploads/latex"):
                os.makedirs("uploads/latex")
            image_file_path = os.path.join("uploads/latex", f"{self.generate_random_string(15)}.png")

            svg2png(bytestring=body, write_to=image_file_path)

            return {
                'direct_result': {
                    'kind': 'photo',
                    'format': 'path',
                    'value': image_file_path
                }
            }

        except HTTPError as e:
            body = e.read()
            return {'result': body if body else "Unable to convert LaTeX"}

        except:
            if 'image_file_path' in locals():
                os.remove(image_file_path)

            return {'result': 'Unable to convert LaTeX'}

    def generate_random_string(self, length):
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for _ in range(length))

