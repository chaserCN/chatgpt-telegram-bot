import re
import datetime
from typing import TypedDict

from .latex_constants import LATEX_PREAMBLE_TEMPLATE


class ParsedLatex(TypedDict):
    """
    Тип для хранения парсинга LaTeX-документа.
    """
    body: str
    lang: str


def parse_ai_latex(ai_latex: str) -> ParsedLatex:
    """
    Извлекает язык и тело документа из полного LaTeX кода.
    """
    content = {
        'body': '', 
        'lang': 'russian' # Язык по умолчанию
    }

    # Извлекаем язык из \usepackage[...]{babel}
    lang_match = re.search(r'\\usepackage\[([^\]]+)\]\{babel\}', ai_latex)
    if lang_match:
        # Берем последний язык из списка, на случай если их несколько (e.g, [english,ukrainian])
        content['lang'] = lang_match.group(1).split(',')[-1].strip()

    # Извлекаем тело документа
    body_match = re.search(r'\\begin\{document\}(.*?)\\end\{document\}', ai_latex, re.DOTALL)
    if body_match:
        body = body_match.group(1)
        # Удаляем команды, которые нам больше не нужны
        body = re.sub(r'\\title\{[^}]+\}', '', body, flags=re.DOTALL)
        body = re.sub(r'\\author\{[^}]+\}', '', body, flags=re.DOTALL)
        body = re.sub(r'\\date\{[^}]+\}', '', body, flags=re.DOTALL)
        body = re.sub(r'\\maketitle', '', body).strip()
        content['body'] = body
        
    return content

def build_latex_for_rendering(content: ParsedLatex) -> str:
    """
    Собирает финальный, готовый к рендерингу LaTeX-документ из словаря с контентом.
    """
    # Используем общую преамбулу, подставляя нужный язык
    latex_preamble = LATEX_PREAMBLE_TEMPLATE.format(lang=content['lang'])

    # Обрабатываем тело: оборачиваем каждую страницу в 'preview'
    body_parts = content['body'].split(r'\newpage')
    processed_body = r'\end{preview}\newpage\begin{preview}'.join(body_parts)
    full_body_with_previews = f"\\begin{{preview}}\n{processed_body}\n\\end{{preview}}"

    # Собираем финальный документ
    final_latex_code = latex_preamble + full_body_with_previews + r"\end{document}"
    
    return final_latex_code
