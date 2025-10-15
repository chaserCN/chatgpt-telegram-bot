import re
import uuid
import html

from .latex_renderer import render_latex_document

def process_full_latex_document(latex_document: str, output_dir: str = '.', debug: bool = False) -> list[str]:
    """
    Обрабатывает полный LaTeX документ, который уже был проверен и очищен.
    """
    # --- Автоматическое добавление пакетов для кириллицы ---
    # Проверяем на наличие кириллических символов (включая украинские)
    if re.search(r'[а-яА-Яїієґ]', latex_document):
        
        # Определяем язык по специфичным украинским буквам
        lang = 'ukrainian' if any(c in 'їієґ' for c in latex_document) else 'russian'
        
        # Проверяем, подключен ли babel
        is_babel_present = re.search(r'\\usepackage.*\{babel\}', latex_document)
        
        required_packages = ""
        if r'\usepackage[T2A]{fontenc}' not in latex_document:
            required_packages += r'\usepackage[T2A]{fontenc}' + '\n'
        
        if not is_babel_present:
            required_packages += f'\\usepackage[{lang}]{{babel}}' + '\n'

        if required_packages:
            # Вставляем пакеты после \documentclass{...}
            
            # Экранируем бэкслэши для re.subn
            repl = required_packages.replace('\\', r'\\')

            latex_document, count = re.subn(
                r'(\\documentclass.*?\{.*?\})',
                r'\g<1>' + '\n' + repl,
                latex_document,
                count=1
            )
            if count == 0:
                 # Если \documentclass не найден, вставляем перед \begin{document}
                latex_document, _ = re.subn(
                    r'(\\begin\{document\})',
                    repl + r' \g<1>',
                    latex_document,
                    count=1
                )

    # --- Безопасное увеличение размера шрифта ---
    # 1. Гарантируем, что используется класс scrartcl
    latex_document = latex_document.replace('{article}', '{scrartcl}')
    latex_document = latex_document.replace('{extarticle}', '{scrartcl}')

    # 2. Используем re.sub с функцией для точечной замены только в \documentclass
    def update_documentclass(match):
        options = match.group(1) or '[]'  # [12pt,a4paper] или []
        doc_class = match.group(2)      # {scrartcl}

        # Удаляем старые размеры шрифта из опций
        cleaned_options = re.sub(r'(\d+)pt,?', '', options)
        
        # Собираем новые опции. Вставляем 16pt в начало.
        # Убираем скобки и пустое пространство, затем собираем заново.
        options_list = [opt.strip() for opt in cleaned_options.strip('[]').split(',') if opt.strip()]
        
        # Добавляем наш размер шрифта, если его еще нет
        if '16pt' not in options_list:
            options_list.insert(0, '16pt')

        final_options_str = f"[{','.join(options_list)}]"
        
        return f"\\documentclass{final_options_str}{doc_class}"

    # Ищем \documentclass с опциями или без
    # и применяем нашу функцию для замены
    pattern = r'\\documentclass(\[.*?\])?(\{.*?\})'
    latex_document, count = re.subn(pattern, update_documentclass, latex_document, count=1)
    
    # Если \documentclass не был найден (очень редкий случай),
    # то ничего не делаем, чтобы не сломать документ.
    if count == 0:
        print("⚠️  Warning: \\documentclass не найден. Размер шрифта не изменен.")


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
    saved_files = render_latex_document(final_latex_code, output_filename, output_dir=output_dir, debug=debug)
    return saved_files
