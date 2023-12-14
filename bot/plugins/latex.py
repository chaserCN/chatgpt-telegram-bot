import io
import os, random, string
import urllib
import urllib.parse
import urllib.request
from typing import Dict
from urllib.error import HTTPError
from cairosvg import svg2png
from PIL import Image

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
            svg_data = resp.read()

            png_data = self.convert2png(svg_data)

            image_file_path = self.save_to_tmp_file(png_data)

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

        except Exception as e:
            return {'result': f"Unable to convert LaTeX: {e}"}

    def save_to_tmp_file(self, data):
        if not os.path.exists("uploads/latex"):
            os.makedirs("uploads/latex")

        image_file_path = os.path.join("uploads/latex", f"{self.generate_random_string(15)}.png")

        with open(image_file_path, 'wb') as file:
            # Write the byte string to the file
            file.write(data)

        return image_file_path

    def generate_random_string(self, length):
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for _ in range(length))

    def convert2png(self, svg_data):
        image_data = svg2png(bytestring=svg_data)
        return self.add_margin(image_data)

    def add_margin(self, image_data):
        image_stream = io.BytesIO(image_data)

        # Open the image using Pillow
        image = Image.open(image_stream)

        # Define the size of the margins you want to add
        margin = 30

        # Create a new image with the desired dimensions including margins
        new_width = image.width + margin + margin
        new_height = image.height + margin + margin

        # Create a new blank image with the calculated dimensions
        new_image = Image.new("RGBA", (new_width, new_height),
                              (255, 255, 255, 0))  # Adjust the color and transparency as needed

        # Paste the original image onto the new image with margins
        new_image.paste(image, (margin, margin))

        # Save the modified image to a BytesIO object
        output_stream = io.BytesIO()
        new_image.save(output_stream, format='PNG')

        # Get the binary data of the modified image
        output_stream.seek(0)
        return output_stream.getvalue()
