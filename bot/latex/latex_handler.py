import re
import os
import uuid
import html
from uuid import uuid4
from pathlib import Path

from .latex_renderer import render_latex_document
from .latex_full_document_handler import process_full_latex_document
from .latex_telegram_parser import convert_telegram_html_to_latex
from .latex_utils import is_valid_latex, format_text_for_display


def is_full_latex_document(text: str) -> bool:
    """
    Проверяет, является ли текст полным LaTeX документом,
    ища \documentclass в любом месте текста.
    """
    return r'\documentclass' in text


def _find_main_latex_document(text: str) -> tuple[str, str, str] | None:
    """
    Finds the first complete LaTeX document block and text around it.
    Returns (text_before, document, text_after) or None.
    """
    # A document is considered to start with \documentclass
    # and contain a document environment.
    match = re.search(
        r"(.*?)(\\documentclass.*?\\end\{document\})(.*)", 
        text, 
        re.DOTALL
    )
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None

def _contains_latex_fragments(text: str) -> bool:
    """Checks for LaTeX math delimiters or commands."""
    # Simplified check for common LaTeX patterns.
    latex_pattern = re.compile(r"""
        \$[^$].*?\$ | # $...$ (inline math)
        \$\$ |        # $$...$$ (display math)
        \\\( | \\\) | # \(...\)
        \\\[ | \\\] | # \[...\]
        \\begin\{     # \begin{...}
    """, re.VERBOSE)
    return bool(latex_pattern.search(text))

def _fix_common_latex_errors(text: str) -> str:
    """
    Исправляет распространенные ошибки в LaTeX коде,
    например, использование \mathbf для кириллического текста.
    """
    # Заменяем \mathbf{кириллица} на \textbf{кириллица}
    # Это исправляет ошибку "Command \CYRA invalid in math mode"
    text = re.sub(r'\\mathbf\{([^{}]*[а-яА-Я][^{}]*)\}', r'\\textbf{\1}', text)
    
    # Заменяем \text{кириллица} на \mbox{кириллица} внутри математического режима
    # Это решает аналогичную проблему для единиц измерения и т.д.
    text = re.sub(r'\\text\{([^{}]*[а-яА-Я][^{}]*)\}', r'\\mbox{\1}', text)
    
    return text


def process_text(text: str, output_dir: str = '.') -> tuple[str, list[str] | str]:
    """
    Обрабатывает входной текст, определяя, является ли он полным
    LaTeX документом или фрагментом, и рендерит его в изображение.
    """
    text = _fix_common_latex_errors(text)
    text = html.unescape(text)

    # Проверяем, является ли текст полным LaTeX документом
    if is_full_latex_document(text):
        print("--- Обнаружен полный LaTeX документ ---")
        
        # Находим начало настоящего LaTeX документа и отрезаем все, что было до него.
        # Это ключевое исправление.
        doc_start_index = text.find(r'\documentclass')
        latex_doc_text = text[doc_start_index:]
        
        saved_files = process_full_latex_document(latex_doc_text, output_dir=output_dir)
        return 'image', saved_files
    else:
        # Если это не полный документ, считаем его одним большим фрагментом
        # и рендерим как единое целое.
        print("--- Обнаружен фрагмент LaTeX. Рендерим как единый документ. ---")
        full_latex_code = convert_telegram_html_to_latex(text)
        output_filename = f"render_{uuid.uuid4()}"
        saved_files = render_latex_document(
            full_latex_code,
            output_filename,
            output_dir=output_dir
        )
        return 'image', saved_files
