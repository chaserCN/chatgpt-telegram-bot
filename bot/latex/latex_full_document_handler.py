import re
import uuid
import html

from .latex_renderer import render_latex_document

def process_full_latex_document(latex_document: str, output_dir: str = '.') -> list[str]:
    """
    Обрабатывает полный LaTeX документ, который уже был проверен и очищен.
    """
    # --- Увеличение размера шрифта через команду \Huge ---
    # Возвращаемся к стандартному 12pt и классу scrartcl
    latex_document = latex_document.replace('{article}', '{scrartcl}')
    latex_document = latex_document.replace('{extarticle}', '{scrartcl}')

    # Устанавливаем базовый размер 12pt
    latex_document, count = re.subn(r'\\documentclass\[', r'\\documentclass[12pt,', latex_document, count=1)
    if count == 0:
        latex_document, count = re.subn(r'\\documentclass\{', r'\\documentclass[12pt]{', latex_document, count=1)

    # Вставляем \Huge после начала документа
    latex_document = latex_document.replace(r'\begin{document}', r'\begin{document}\Huge', 1)


    # --- Исправление конфликта пакета Babel ---
    # Unescape HTML entities
    latex_document = html.unescape(latex_document)

    # Add shorthands=off to babel options
    latex_document = re.sub(r'\\usepackage\[(.*?)\]\{babel\}', r'\\usepackage[\1,shorthands=off]{babel}', latex_document)
    latex_document = latex_document.replace(r'\usepackage{babel}', r'\usepackage[shorthands=off]{babel}')

    # --- Preamble Injection Logic ---
    preamble_additions = []

    # 1. Add caption if needed
    if r'\captionof' in latex_document and r'\usepackage{caption}' not in latex_document:
        preamble_additions.append(r'\usepackage{caption}')

    # 2. Add fancyhdr to robustly disable page numbers
    if r'\usepackage{fancyhdr}' not in latex_document:
        preamble_additions.append(r'\usepackage{fancyhdr}')
    
    # This is the known working block from telegram_parser
    preamble_additions.append(r"""
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancypagestyle{plain}{
  \fancyhf{}
  \renewcommand{\headrulewidth}{0pt}
  \renewcommand{\footrulewidth}{0pt}
}
""")
    
    injection_str = "\n".join(preamble_additions) + "\n"

    # Use re.sub to replace only the first occurrence of \begin{document}
    # This is safer than multiple .replace() calls.
    # We must escape backslashes in the replacement string for re.sub
    repl_string = injection_str.replace('\\', r'\\') + r'\\begin{document}'
    latex_document, num_replacements = re.subn(r'\\begin\{document\}', repl_string, latex_document, count=1)

    if num_replacements == 0:
        print("⚠️  Warning: \\begin{document} not found. Could not inject preamble fixes.")

    final_latex_code = latex_document
    
    output_filename = f"render_{uuid.uuid4()}"
    saved_files = render_latex_document(final_latex_code, output_filename, output_dir=output_dir)
    return saved_files
