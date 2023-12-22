import io
import os, random, string
import urllib
import urllib.parse
import urllib.request
from typing import Dict
import matplotlib.pyplot as plt
from cairosvg import svg2png
from PIL import Image
from PIL import ImageOps
from .plugin import Plugin, generate_random_string


class LatexConverterPlugin(Plugin):
    """
    A plugin to answer questions using WolframAlpha.
    """

    def get_source_name(self) -> str:
        return "LatexConverter"

    def get_icon(self) -> str:
        return "🖌️"

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
        try:
            svg_data = self.latex_to_svg(kwargs['from'])
            png_data = self.svg_to_png(svg_data)

            image_file_path = self.save_to_tmp_file(png_data)

            return {
                'direct_result': {
                    'kind': 'photo',
                    'format': 'path',
                    'value': image_file_path
                }
            }

        except Exception as e:
            return {'result': f"Unable to convert LaTeX: {e}"}

    def save_to_tmp_file(self, data):
        if not os.path.exists("uploads/latex"):
            os.makedirs("uploads/latex")

        image_file_path = os.path.join("uploads/latex", f"{generate_random_string(15)}.png")

        with open(image_file_path, 'wb') as file:
            # Write the byte string to the file
            file.write(data)

        return image_file_path

    def latex_to_svg(self, latex_expression):
        if not latex_expression.startswith('$'):
            latex_expression = '$' + latex_expression
        if not latex_expression.endswith('$'):
            latex_expression += '$'

        fig, ax = plt.subplots(figsize=(4, 3))

        ax.text(0.5, 0.5, latex_expression, size=30, ha='center', va='center')

        ax.axis('off')

        buffer = io.BytesIO()
        plt.savefig(buffer, format="svg", bbox_inches='tight', pad_inches=0)
        buffer.seek(0)
        svg_data = buffer.getvalue()
        buffer.close()

        plt.close()

        return svg_data

    def svg_to_png(self, svg_data):
        image_data = svg2png(bytestring=svg_data)
        return self.add_margin(image_data)

    def add_margin(self, image_data):
        image_stream = io.BytesIO(image_data)

        # Open the image using Pillow
        image = Image.open(image_stream)

        # Define the size of the margins you want to add
        margin = 30

        new_image = ImageOps.expand(image, border=margin, fill=(255, 255, 255, 0))

        # Save the modified image to a BytesIO object
        output_stream = io.BytesIO()
        new_image.save(output_stream, format='PNG')

        # Get the binary data of the modified image
        output_stream.seek(0)
        return output_stream.getvalue()

