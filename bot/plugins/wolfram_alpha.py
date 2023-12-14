import os
import urllib
import urllib.parse
import urllib.request
from typing import Dict
from urllib.error import HTTPError

import wolframalpha

from .plugin import Plugin


class WolframAlphaPlugin(Plugin):
    """
    A plugin to answer questions using WolframAlpha.
    """
    def __init__(self):
        wolfram_app_id = os.getenv('WOLFRAM_APP_ID')
        if not wolfram_app_id:
            raise ValueError('WOLFRAM_APP_ID environment variable must be set to use WolframAlphaPlugin')
        self.app_id = wolfram_app_id

    def get_source_name(self) -> str:
        return "WolframAlpha"

    def get_spec(self) -> [Dict]:
        return [{
            "name": "answer_with_wolfram_alpha",
            "description": "Get an answer to a question using Wolfram Alpha. Input should the the query in English.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query, in english (translate if necessary)"}
                },
                "required": ["query"]
            }
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        data = dict(
            input=kwargs['query'],
            appid=self.app_id,
        )

        query = urllib.parse.urlencode(data)
        url = 'https://www.wolframalpha.com/api/v1/llm-api?' + query

        try:
            resp = urllib.request.urlopen(url)
            body = resp.read()
        except HTTPError as e:
            body = e.read()

        return {'answer': body}
