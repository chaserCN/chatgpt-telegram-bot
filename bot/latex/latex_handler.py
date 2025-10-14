import re
import os
from uuid import uuid4

from .latex_doc_parser import parse_ai_latex, build_latex_for_rendering
from .latex_renderer import render_latex_document
from .latex_telegram_format_parser import convert_telegram_html_to_latex, escape_latex

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

def process_text(input_text: str, output_dir: str = 'output') -> tuple[str, str | list[str]]:
    """
    Processes an input string to detect and render LaTeX.

    Args:
        input_text: The string to process.
        output_dir: The directory to save rendered images.

    Returns:
        A tuple of (type, content):
        - ('image', ['/path/to/image1.png', ...]) if rendered.
        - ('text', 'original text') if no LaTeX is found.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    doc_parts = _find_main_latex_document(input_text)

    # Case 1: Full LaTeX document found
    if doc_parts:
        before_text, latex_document, after_text = doc_parts
        
        parsed_content = parse_ai_latex(latex_document)
        
        extra_text_parts = []
        if before_text.strip():
            extra_text_parts.append(escape_latex(before_text.strip()))
        if after_text.strip():
            extra_text_parts.append(escape_latex(after_text.strip()))
        
        extra_text = r" \par ".join(extra_text_parts)
            
        if extra_text:
            # Prepend extra text to the existing body
            parsed_content['body'] = extra_text + r" \newpage " + parsed_content.get('body', '')

        final_latex = build_latex_for_rendering(parsed_content)
        
        output_prefix = f"render_{uuid4()}"
        image_paths = render_latex_document(final_latex, output_prefix, output_dir)
        return ('image', image_paths)

    # Case 2: LaTeX fragments found (but not a full doc)
    elif _contains_latex_fragments(input_text):
        final_latex = convert_telegram_html_to_latex(input_text)
        
        output_prefix = f"render_{uuid4()}"
        image_paths = render_latex_document(final_latex, output_prefix, output_dir)
        return ('image', image_paths)
        
    # Case 3: No LaTeX found
    else:
        return ('text', input_text)
