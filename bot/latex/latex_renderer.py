import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from pdf2image import convert_from_bytes

def render_latex_document(full_latex_code: str, output_prefix: str, output_dir: str = '.'):
    """
    Рендерит полный LaTeX документ, сохраняя каждую страницу 
    как отдельное, обрезанное изображение в указанную директорию.
    """
    # Убедимся, что выходная директория существует
    os.makedirs(output_dir, exist_ok=True)

    print("1. Создаем временные файлы...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        tex_filename = temp_path / "document.tex"
        pdf_filename = temp_path / "document.pdf"

        # Создаем подпапку для шрифтов
        temp_font_dir = temp_path / "Fonts"
        os.makedirs(temp_font_dir)

        # Копируем шрифты
        font_dir = "Fonts"
        print(f"2. Копируем шрифты из '{font_dir}' в '{temp_font_dir}'...")
        if os.path.isdir(font_dir):
            for font_file in os.listdir(font_dir):
                if font_file.upper().endswith(".TTF"):
                    shutil.copy(os.path.join(font_dir, font_file), temp_font_dir)
        else:
            print(f"   - Внимание: Папка '{font_dir}' не найдена.")

        with open(tex_filename, "w", encoding="utf-8") as f:
            f.write(full_latex_code)
        print(f"   - Создан {tex_filename}")

        # Запускаем lualatex
        print("3. Запускаем lualatex...")
        for i in range(2):
            print(f"   - Проход {i+1}/2...")
            result = subprocess.run(
                ["/usr/local/texlive/2025basic/bin/universal-darwin/lualatex", "-interaction=nonstopmode", tex_filename.name],
                cwd=temp_path, capture_output=True, text=True, encoding='utf-8'
            )
            if result.returncode != 0:
                print("\n--- ОШИБКА КОМПИЛЯЦИИ LUALATEX ---")
                print(result.stdout)
                print("---------------------------------\n")
                with open("debug_error.tex", "w", encoding='utf-8') as f:
                    f.write(full_latex_code)
                print("⚠️  Исходный .tex файл сохранен как 'debug_error.tex'.")
                return

        if not pdf_filename.exists():
            print(f"❌ Рендеринг не удался! lualatex не создал PDF.")
            print("Содержимое временной папки:")
            for f in temp_path.iterdir(): print(f"  - {f.name}")
            return

        print("4. Конвертируем PDF в изображение...")
        images = convert_from_bytes(pdf_filename.read_bytes(), dpi=200)
        print(f"   - Получено страниц: {len(images)}")

        if not images:
            print("❌ Рендеринг не удался! PDF не удалось конвертировать.")
            return []

        print(f"5. Сохраняем страницы как отдельные файлы в '{output_dir}'...")
        saved_files = []
        for i, img in enumerate(images):
            output_filename = os.path.join(output_dir, f"{output_prefix}_page_{i+1}.png")
            img.save(output_filename, optimize=True)
            print(f"   - Создан файл '{output_filename}'")
            saved_files.append(output_filename)
        
        print(f"\n✅ Рендеринг успешно завершен!")
        print(f"📁 Проверьте итоговые файлы с префиксом '{output_prefix}'")
        return saved_files
