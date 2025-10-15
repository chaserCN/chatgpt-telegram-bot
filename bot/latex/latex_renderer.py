import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from pdf2image import convert_from_bytes
from PIL import Image
import numpy as np

def _crop_image_with_numpy(img: Image.Image, padding: int = 20) -> Image.Image | None:
    """
    Обрезает изображение, удаляя белые поля с помощью numpy.
    Возвращает None, если изображение полностью белое.
    """
    # Convert image to numpy array
    np_array = np.array(img)
    
    # Find non-white pixels (assuming white is [255, 255, 255])
    # For grayscale, the check would be different
    if len(np_array.shape) == 3: # RGB or RGBA
        non_white_pixels = np.any(np_array[:, :, :3] != 255, axis=2)
    else: # Grayscale
        non_white_pixels = np_array != 255
        
    # Проверяем, есть ли вообще не-белые пиксели
    if not np.any(non_white_pixels):
        print("   - Обнаружена полностью белая страница, пропускаем.")
        return None

    # Get the bounding box of the non-white pixels
    rows = np.any(non_white_pixels, axis=1)
    cols = np.any(non_white_pixels, axis=0)
    
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]

    # Обрезаем и добавляем отступы
    rmin = max(0, rmin - padding)
    rmax = min(img.height, rmax + padding)
    cmin = max(0, cmin - padding)
    cmax = min(img.width, cmax + padding)
    
    return img.crop((cmin, rmin, cmax, rmax))


def render_latex_document(full_latex_code: str, output_prefix: str, output_dir: str = '.') -> list[str]:
    """
    Рендерит полный LaTeX документ, сохраняя каждую страницу 
    как отдельное, обрезанное изображение в указанную директорию.
    """
    # Create the output directory if it doesn't exist
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        tex_path = temp_path / "document.tex"
        pdf_path = temp_path / "document.pdf"
        
        print("\n--- РЕНДЕРИНГ LATEX ---")
        print("1. Создаем временные файлы...")
            
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(full_latex_code)
        print(f"   - Создан {tex_path}")

        # --- Dynamically find TeX executables ---
        pdflatex_path = shutil.which("pdflatex") or "/Library/TeX/texbin/pdflatex"
        tex_bin_path = Path(pdflatex_path).parent
        pdfcrop_path = tex_bin_path / "pdfcrop"
        # ---

        print("2. Запускаем pdflatex...")

        # Run pdflatex twice for references and layout
        for i in range(2):
            print(f"   - Проход {i+1}/2...")
            
            result = subprocess.run(
                [pdflatex_path, "-interaction=nonstopmode", tex_path.name],
                cwd=temp_path, capture_output=True, text=True, encoding='utf-8', errors='ignore'
            )
            
            print(f"   - Содержимое временной папки после прохода {i+1}:")
            for f in temp_path.iterdir():
                print(f"     - {f.name}")

            # Продолжаем, даже если есть ошибки, но PDF файл был создан.
            # Некоторые ошибки, как \ce, не фатальны.
            if result.returncode != 0 and not pdf_path.exists():
                print("--- ОШИБКА КОМПИЛЯЦИИ PDFlatex ---")
                print("--- STDOUT ---")
                print(result.stdout)
                print("--- STDERR ---")
                print(result.stderr)
                # Log file might contain more details
                # log_path = temp_path / "document.log"
                # if log_path.exists():
                #     print("--- document.log ---")
                #     print(log_path.read_text(encoding='utf-8', errors='ignore'))
                        
                # # Сохраняем .tex для дебага
                # debug_file_path = Path(output_dir) / f"debug_{output_prefix}.tex"
                # with open(debug_file_path, 'w', encoding='utf-8') as f:
                #     f.write(full_latex_code)
                # print("⚠️  Исходный .tex файл сохранен как 'debug_error.tex'.")
                # return [] # Возвращаем пустой список, если PDF не создан

        if not pdf_path.exists():
            print(f"❌ Рендеринг не удался! pdflatex не создал PDF.")
            print("Содержимое временной папки:")
            for f in temp_path.iterdir(): print(f"  - {f.name}")
            return []

        print("3. Конвертируем PDF в изображение...")
        images = convert_from_bytes(pdf_path.read_bytes(), dpi=300)
        print(f"   - Получено страниц: {len(images)}")

        # Crop images and save
        saved_files = []
        if images:
            print(f"4. Обрезаем пустые поля у каждой страницы...")
            # Обрезаем и сразу отфильтровываем пустые (None) страницы
            cropped_images = [
                cropped for i in images
                if (cropped := _crop_image_with_numpy(i, padding=40)) is not None
            ]

            final_images = cropped_images

            print(f"5. Сохраняем и обрабатываем страницы в '{output_dir}'...")
            for i, img in enumerate(final_images):
                img_path = Path(output_dir) / f"{output_prefix}_page_{i+1}.png"

                # --- Изменение размера изображения с помощью Pillow ---
                # Устанавливаем максимальную ширину, чтобы избежать слишком больших картинок
                max_width = 1080
                if img.width > max_width:
                    # Рассчитываем новую высоту для сохранения пропорций
                    aspect_ratio = img.height / img.width
                    new_height = int(max_width * aspect_ratio)
                    
                    # Изменяем размер с использованием качественного фильтра
                    resized_img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                    resized_img.save(img_path)
                    print(f"   - Сохранено и уменьшено до {max_width}x{new_height}: {img_path}")
                else:
                    img.save(img_path)
                    print(f"   - Сохранено (размер {img.width}x{img.height}): {img_path}")

                saved_files.append(str(img_path))
        else:
            print("❌ Рендеринг не удался! PDF не удалось конвертировать.")
            return []

        print(f"\n✅ Рендеринг успешно завершен!")
        print(f"📁 Проверьте итоговые файлы с префиксом '{output_prefix}'")
        return saved_files
