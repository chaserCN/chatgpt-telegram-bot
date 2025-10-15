import re
from bs4 import BeautifulSoup, NavigableString
import uuid

# Этот шаблон используется для создания полноценного LaTeX документа
# из фрагментов, найденных в тексте.
_LATEX_PREAMBLE_TEMPLATE_RAW = r"""
\documentclass[12pt]{scrartcl}
\usepackage[utf8]{inputenc}
\usepackage[T2A]{fontenc}
\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage{{graphicx}}
\usepackage{{hyperref}}
\usepackage{{tikz}}
\usetikzlibrary{{angles,quotes}}
\usepackage{{caption}}
\usepackage{{titlesec}}
\usepackage[shorthands=off,{lang}]{{babel}} 
\usepackage{{microtype}} % Improves typography and spacing
\usepackage{{cancel}}
\usepackage{{ulem}}
\usepackage{{xcolor}}
\usepackage{{mhchem}}
\usepackage{{geometry}}
\geometry{{a4paper, margin=1in}}

\pagenumbering{{gobble}}
"""
LATEX_PREAMBLE_TEMPLATE = _LATEX_PREAMBLE_TEMPLATE_RAW.replace('{{lang}}', '{lang}')


# Словарь для экранирования специальных символов LaTeX
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


def convert_telegram_html_to_latex(html_text: str, lang: str = 'russian') -> str:
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
    body_content = _recursive_html_to_latex(soup)

    # Restore placeholders
    for i, item in enumerate(latex_parts):
        # Просто восстанавливаем LaTeX фрагменты без всяких оберток
        body_content = body_content.replace(f'LATEX_TOKEN_{i}', item, 1)

    for i, item in enumerate(pre_parts):
        body_content = body_content.replace(f'__PRE_BLOCK_{i}__', f'\\begin{{verbatim}}\n{item}\n\\end{{verbatim}}', 1)

    # 9. Собираем финальный документ
    # Определяем язык (пока что статически, можно расширить)
    # Простой эвристический метод для определения языка
    # lang = 'ukrainian' if any(c in 'їієґ' for c in html_text) else 'russian' # This line is removed as per the new_code
    
    # Аккуратно форматируем только плейсхолдер {lang}
    preamble = LATEX_PREAMBLE_TEMPLATE.format(lang=lang)
    body = process_document_body(html_text) # Используем новую функцию
    return f"{preamble}\\begin{{document}}\\Huge\n{body}\n\\end{{document}}"
