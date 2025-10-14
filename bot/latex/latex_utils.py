"""
Вспомогательные функции для LaTeX конвертера
"""

import re
from typing import List, Dict, Any


def clean_html_entities(text: str) -> str:
    """Очищает HTML entities в тексте"""
    entities = {
        '&lt;': '<',
        '&gt;': '>',
        '&amp;': '&',
        '&quot;': '"',
        '&#39;': "'",
        '&nbsp;': ' '
    }
    
    for entity, replacement in entities.items():
        text = text.replace(entity, replacement)
    
    return text


def extract_telegram_links(text: str) -> List[Dict[str, str]]:
    """Извлекает ссылки из Telegram HTML"""
    link_pattern = r'<a href="([^"]+)">([^<]+)</a>'
    links = []
    
    for match in re.finditer(link_pattern, text):
        links.append({
            'url': match.group(1),
            'text': match.group(2)
        })
    
    return links


def extract_telegram_emojis(text: str) -> List[Dict[str, str]]:
    """Извлекает эмодзи из Telegram HTML"""
    emoji_pattern = r'<tg-emoji emoji-id="([^"]+)">([^<]+)</tg-emoji>'
    emojis = []
    
    for match in re.finditer(emoji_pattern, text):
        emojis.append({
            'emoji_id': match.group(1),
            'fallback': match.group(2)
        })
    
    return emojis


def is_valid_latex(latex_text: str) -> bool:
    """Проверяет, является ли текст валидной LaTeX формулой"""
    # Простая проверка на наличие LaTeX команд
    latex_commands = [
        r'\\frac', r'\\sqrt', r'\\sum', r'\\int', r'\\lim',
        r'\\alpha', r'\\beta', r'\\gamma', r'\\delta', r'\\pi',
        r'\\infty', r'\\pm', r'\\mp', r'\\times', r'\\div',
        r'\\leq', r'\\geq', r'\\neq', r'\\approx', r'\\equiv'
    ]
    
    for pattern in latex_commands:
        if re.search(pattern, latex_text):
            return True
    
    # Проверяем на математические символы
    math_symbols = r'[+\-*/=<>()\[\]{}^_]'
    if re.search(math_symbols, latex_text):
        return True
    
    return False


def format_text_for_display(text: str) -> str:
    """Форматирует текст для отображения (убирает лишние пробелы и переносы)"""
    # Убираем лишние пробелы
    text = re.sub(r'\s+', ' ', text)
    
    # Убираем пробелы в начале и конце
    text = text.strip()
    
    # Заменяем множественные переносы строк на одинарные
    text = re.sub(r'\n\s*\n', '\n', text)
    
    return text

