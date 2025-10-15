import re
from bs4 import BeautifulSoup, NavigableString
import uuid
import html


# Этот шаблон используется для создания полноценного LaTeX документа
# из фрагментов, найденных в тексте.
_LATEX_PREAMBLE_TEMPLATE_RAW = r"""
\documentclass[12pt]{{scrartcl}}
\usepackage[utf8]{{inputenc}}
\usepackage[T2A]{{fontenc}}
\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage{{graphicx}}
\usepackage{{hyperref}}
\usepackage{{tikz}}
\usetikzlibrary{{angles,quotes}}
\usepackage{{caption}}
\usepackage{{titlesec}}
\titleformat{{\section}}{{\Huge\bfseries}}{{\thesection}}{{1em}}{{}}
\usepackage[shorthands=off,{{lang}}]{{babel}} 
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
    '<': r'\textless{}',
    '>': r'\textgreater{}',
    '"': r"''", # Простой способ экранировать кавычки
}

# Регулярное выражение для поиска всех видов LaTeX-вставок
# [\s\S] позволяет $$...$$ быть многострочным. [^\n\$] заставляет $...$ быть однострочным.
LATEX_REGEX = re.compile(r'(\$\$[\s\S]+?\$\$|\$[^\n\$]+?\$|\\\[[\s\S]+?\\\]|\\\([^\n]+?\\\))')

def escape_latex(text: str) -> str:
    """Экранирует специальные LaTeX символы в строке."""
    return "".join(LATEX_SPECIAL_CHARS.get(char, char) for char in text)


def _recursive_html_to_latex(tag) -> str:
    """Рекурсивно обходит дерево BeautifulSoup и конвертирует его в LaTeX."""
    if isinstance(tag, NavigableString):
        # На этом этапе мы просто экранируем ВЕСЬ текст.
        # Формулы будут восстановлены позже.
        text_to_escape = str(tag)
        escaped_text = escape_latex(text_to_escape)
        return escaped_text

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
        return "__BR_TAG__" # Используем плейсхолдер
        
    # Для неизвестных тегов (вроде span, body, html) просто возвращаем их содержимое
    return content


def convert_telegram_html_to_latex(html_text: str) -> str:
    """
    Конвертирует HTML-разметку Telegram в полноценный LaTeX документ.
    """
    # 1. СНАЧАЛА "разэкранируем" HTML сущности. ЭТО КЛЮЧ.
    text_unscaped = html.unescape(html_text)

    # 2. Проверяем, не является ли это уже полноценным документом
    if r'\documentclass' in text_unscaped and r'\begin{document}' in text_unscaped:
        return text_unscaped

    # 3. Изолируем <pre> блоки, так как их содержимое не должно парситься
    pre_parts = []
    pre_regex = re.compile(r'(<pre>.*?</pre>)', re.DOTALL)
    def isolate_pre_match(match):
        placeholder = f"__PRE_BLOCK_{len(pre_parts)}__"
        pre_soup = BeautifulSoup(match.group(0), 'html.parser')
        pre_parts.append(pre_soup.get_text())
        return placeholder
    
    html_no_pre = pre_regex.sub(isolate_pre_match, text_unscaped)

    # 4. Парсим HTML и экранируем ВЕСЬ текстовый контент
    soup = BeautifulSoup(f"<body>{html_no_pre}</body>", 'html.parser')
    body_content = _recursive_html_to_latex(soup)

    # 5. Восстанавливаем <pre> плейсхолдеры
    for i, item in enumerate(pre_parts):
        # Заменяем плейсхолдеры на \begin{verbatim}, плейсхолдеры не были экранированы
        body_content = body_content.replace(f'__PRE_BLOCK_{i}__'.replace('_', r'\_'), f'\\begin{{verbatim}}\n{item}\n\\end{{verbatim}}', 1)

    # 6. Теперь "разэкранируем" LaTeX-формулы, которые были экранированы на шаге 4
    # Мы ищем оригинальные формулы в исходном, неэкранированном тексте
    # и заменяем их экранированные версии в body_content.
    latex_matches = LATEX_REGEX.finditer(text_unscaped)
    for match in latex_matches:
        original_formula = match.group(0)
        escaped_formula = escape_latex(original_formula)
        if escaped_formula in body_content:
            body_content = body_content.replace(escaped_formula, original_formula)
    
    # Заменяем плейсхолдер для <br> на реальный перенос строки LaTeX
    body_content = body_content.replace("__BR_TAG__", r'\\')


    # 7. Собираем финальный документ
    lang = 'ukrainian' if any(c in 'їієґ' for c in html_text) else 'russian'
    preamble = _LATEX_PREAMBLE_TEMPLATE_RAW.format(lang=lang)
    final_latex = f"{preamble}\\begin{{document}}\\Huge\n{body_content}\n\\end{{document}}"
    return final_latex
