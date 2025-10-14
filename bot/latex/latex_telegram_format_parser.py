import re
from bs4 import BeautifulSoup, NavigableString
from typing import TypedDict

from .latex_constants import LATEX_PREAMBLE_TEMPLATE

# A more specific TypedDict for Telegram HTML parsing results
class TelegramParsedLatex(TypedDict):
    pass

# Словарь для замены спецсимволов LaTeX в обычном тексте
LATEX_SPECIAL_CHARS = {
    '&': r'\&',
    '%': r'\%',
    '$': r'\$',
    '#': r'\#',
    '_': r'\_',
    '{': r'\{',
    '}': r'\}',
    '~': r'\textasciitilde{}',
    '^': r'\textasciicircum{}',
    '\\': r'\textbackslash{}',
}

def escape_latex(text: str) -> str:
    """Экранирует специальные LaTeX символы в строке."""
    return "".join(LATEX_SPECIAL_CHARS.get(char, char) for char in text)

def _recursive_html_to_latex(tag) -> str:
    """Рекурсивно обходит дерево BeautifulSoup и конвертирует его в LaTeX."""
    if isinstance(tag, NavigableString):
        # Экранируем, только если это не наш плейсхолдер
        text = str(tag)
        if "LATEX_TOKEN_" in text or "__PRE_BLOCK_" in text:
            return text
        return escape_latex(text)

    # Для <pre> тегов прекращаем рекурсивный обход и возвращаем плейсхолдер
    if tag.name == 'pre':
        # Этот код больше не нужен здесь, так как <pre> изолированы заранее
        # Мы просто должны найти соответствующий плейсхолдер в его содержимом
        return "".join(_recursive_html_to_latex(child) for child in tag.contents)

    content = "".join(_recursive_html_to_latex(child) for child in tag.contents)

    tag_map = {
        'b': r'\textbf{{{content}}}',
        'strong': r'\textbf{{{content}}}',
        'i': r'\textit{{{content}}}',
        'em': r'\textit{{{content}}}',
        'u': r'\underline{{{content}}}',
        'ins': r'\underline{{{content}}}',
        's': r'\sout{{{content}}}',
        'strike': r'\sout{{{content}}}',
        'del': r'\sout{{{content}}}',
        'code': r'\texttt{{{content}}}',
        'blockquote': r'\begin{{quote}}{content}\end{{quote}}',
        'tg-spoiler': r'\textcolor{{lightgray}}{{{content}}}', # Имитация спойлера
    }

    if tag.name in tag_map:
        return tag_map[tag.name].format(content=content)
    
    if tag.name == 'a':
        url = tag.get('href', '')
        return f"\\href{{{url}}}{{{content}}}"
    
    if tag.name == 'br':
        return r'\\\\'
        
    # Для неизвестных тегов (вроде span, body, html) просто возвращаем их содержимое
    return content


def convert_telegram_html_to_latex(html_text: str) -> str:
    """
    Конвертирует HTML-разметку Telegram в полноценный LaTeX документ.
    Если входной текст уже является LaTeX-документом, возвращает его без изменений.
    """
    # 1. Проверяем, не является ли это уже полноценным документом
    if r'\documentclass' in html_text and r'\begin{document}' in html_text:
        return html_text

    # 2. Изолируем <pre> блоки, чтобы защитить их от обработки
    pre_parts = []
    pre_regex = re.compile(r'(<pre>.*?</pre>)', re.DOTALL)
    def isolate_pre_match(match):
        placeholder = f"__PRE_BLOCK_{len(pre_parts)}__"
        pre_parts.append(match.group(0))
        return placeholder
    
    html_no_pre = pre_regex.sub(isolate_pre_match, html_text)

    # 3. Изолируем LaTeX-вставки
    latex_parts = []
    # Регулярное выражение для поиска всех видов LaTeX-разделителей
    latex_regex = re.compile(r'(\$\$[^\$]+\$\$|\$[^\$]+\$|\\\[.+?\\\]|\\\(.+?\\\))', re.DOTALL)
    
    def isolate_latex_match(match):
        placeholder = f"LATEX_TOKEN_{len(latex_parts)}"
        latex_parts.append(match.group(0))
        return placeholder

    sanitized_html = latex_regex.sub(isolate_latex_match, html_no_pre)

    # 4. Экранируем HTML сущности, которые не являются частью тегов
    # BeautifulSoup делает это автоматически при парсинге, так что ручное экранирование не нужно.

    # 5. Парсим HTML с помощью BeautifulSoup
    # 'html.parser' - встроенный, не требует lxml
    soup = BeautifulSoup(f"<body>{sanitized_html}</body>", 'html.parser')

    # 6. Рекурсивно конвертируем дерево в LaTeX
    latex_body = _recursive_html_to_latex(soup.body)
    
    # 6. Восстанавливаем LaTeX на место "ярлыков"
    for i, part in enumerate(latex_parts):
        latex_body = latex_body.replace(f"LATEX_TOKEN_{i}", part)

    # 7. Оборачиваем каждую страницу (разделенную \n\n) в preview ДО восстановления <pre>
    pages = latex_body.split('\n\n')
    processed_pages = r'\end{preview}\newpage\begin{preview}'.join(pages)
    full_body_with_previews = f"\\begin{{preview}}\n{processed_pages}\n\\end{{preview}}"

    # 8. Восстанавливаем <pre> блоки в уже готовую структуру страниц
    for i, part in enumerate(pre_parts):
        pre_soup = BeautifulSoup(part, 'html.parser')
        pre_content = pre_soup.pre.get_text() if pre_soup.pre else ''
        latex_pre_block = f"\\begin{{verbatim}}\n{pre_content.strip()}\n\\end{{verbatim}}"
        full_body_with_previews = full_body_with_previews.replace(f"__PRE_BLOCK_{i}__", latex_pre_block)

    # 9. Собираем финальный документ
    # Определяем язык (пока что статически, можно расширить)
    # Простой эвристический метод для определения языка
    lang = 'ukrainian' if any(c in 'їієґ' for c in html_text) else 'russian'
    
    latex_preamble = LATEX_PREAMBLE_TEMPLATE.format(lang=lang)

    return latex_preamble + full_body_with_previews + r"\end{document}"
