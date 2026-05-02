import base64
import html
from html.parser import HTMLParser
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

import fitz
import requests
from PIL import Image


fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)


PROMPT_ECHO_PATTERNS = [
    "mantieni la struttura del documento",
    "estrai testo",
    "converti le tabelle",
    "non usare html",
    "descrivi sinteticamente le figure",
    "non inventare contenuto",
    "usa il placeholder",
    "restituisci solo markdown",
]


@dataclass
class PageImage:
    page_id: int
    page_number: int
    source_name: str
    image: Image.Image


class HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None
        self.cell_tag_stack: list[str] = []
        self.in_li = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self.close_current_cell()
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            self.close_current_cell()
            self.current_cell = []
            self.cell_tag_stack.append(tag)
        elif tag == "br" and self.current_cell is not None:
            self.current_cell.append("\n")
        elif tag == "li" and self.current_row is not None:
            if self.current_cell is None:
                self.current_cell = []
            if self.current_cell and not self.current_cell[-1].endswith(("\n", " ")):
                self.current_cell.append("\n")
            self.current_cell.append("- ")
            self.in_li = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self.close_current_cell()
        elif tag == "tr" and self.current_row is not None:
            self.close_current_cell()
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None
        elif tag == "li" and self.current_cell is not None:
            self.current_cell.append("\n")
            self.in_li = False

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)

    def close_current_cell(self) -> None:
        if self.current_row is not None and self.current_cell is not None:
            text = normalize_table_cell("".join(self.current_cell))
            self.current_row.append(text)
            self.current_cell = None
            if self.cell_tag_stack:
                self.cell_tag_stack.pop()


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def image_to_base64(image: Image.Image) -> str:
    return base64.b64encode(image_to_png_bytes(image)).decode("ascii")


def normalize_table_cell(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "<br>", text.strip())
    return text.replace("|", r"\|")


def rows_to_markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""

    column_count = max(len(row) for row in rows)
    normalized_rows = [
        row + [""] * (column_count - len(row))
        for row in rows
    ]

    header = normalized_rows[0]
    body = normalized_rows[1:] or [[""] * column_count]
    separator = ["---"] * column_count
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def convert_html_table_to_markdown(table_html: str) -> str:
    parser = HtmlTableParser()
    parser.feed(table_html)
    parser.close()
    markdown_table = rows_to_markdown_table(parser.rows)
    return markdown_table or table_html


def convert_html_tables_to_markdown(markdown: str) -> str:
    return re.sub(
        r"<table\b[^>]*>.*?</table>",
        lambda match: convert_html_table_to_markdown(match.group(0)),
        markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )


def normalize_ocr_markdown(markdown: str) -> str:
    return convert_html_tables_to_markdown(markdown).strip()


def looks_like_prompt_echo(markdown: str) -> bool:
    normalized = re.sub(r"\s+", " ", markdown.lower())
    matches = sum(1 for pattern in PROMPT_ECHO_PATTERNS if pattern in normalized)
    repeated_prompt_terms = normalized.count("struttura del documento") >= 3
    return matches >= 2 or repeated_prompt_terms


def load_image_file(file) -> Image.Image:
    image = Image.open(file)
    return image.convert("RGB")


def parse_page_ranges(page_range: str, total_pages: int) -> list[int]:
    cleaned = page_range.strip()
    if not cleaned:
        return list(range(1, total_pages + 1))

    selected_pages: set[int] = set()
    for raw_part in cleaned.split(","):
        part = raw_part.strip()
        if not part:
            continue

        if "-" in part:
            bounds = [value.strip() for value in part.split("-", 1)]
            if len(bounds) != 2 or not bounds[0].isdigit() or not bounds[1].isdigit():
                raise ValueError(f"Range pagine non valido: {part}")
            start, end = int(bounds[0]), int(bounds[1])
            if start > end:
                raise ValueError(f"Range pagine invertito: {part}")
            selected_pages.update(range(start, end + 1))
        else:
            if not part.isdigit():
                raise ValueError(f"Pagina non valida: {part}")
            selected_pages.add(int(part))

    if not selected_pages:
        raise ValueError("Nessuna pagina selezionata.")

    out_of_bounds = [page for page in sorted(selected_pages) if page < 1 or page > total_pages]
    if out_of_bounds:
        raise ValueError(
            f"Pagine fuori range: {', '.join(str(page) for page in out_of_bounds)}. "
            f"Il documento ha {total_pages} pagine."
        )

    return sorted(selected_pages)


def pdf_to_images(file, dpi: int, page_range: str) -> list[tuple[int, Image.Image]]:
    pdf_bytes = file.getvalue()
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    images: list[tuple[int, Image.Image]] = []

    try:
        fitz.TOOLS.reset_mupdf_warnings()
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            selected_pages = parse_page_ranges(page_range, document.page_count)
            for page_number in selected_pages:
                page = document[page_number - 1]
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")
                images.append((page_number, image))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    except fitz.FileDataError as exc:
        raise RuntimeError("PDF non leggibile o danneggiato.") from exc
    except fitz.FileNotFoundError as exc:
        raise RuntimeError("PDF non trovato.") from exc
    except RuntimeError as exc:
        warnings = fitz.TOOLS.mupdf_warnings().strip()
        details = f" Dettagli MuPDF: {warnings}" if warnings else ""
        raise RuntimeError(f"Errore durante la conversione del PDF.{details}") from exc

    return images


def uploaded_files_to_pages(files: Iterable, dpi: int, page_range: str) -> list[PageImage]:
    pages: list[PageImage] = []
    page_id = 0

    for uploaded_file in files:
        name = uploaded_file.name
        content_type = uploaded_file.type or ""
        extension = Path(name).suffix.lower()

        if content_type == "application/pdf" or extension == ".pdf":
            try:
                pdf_images = pdf_to_images(uploaded_file, dpi, page_range)
            except RuntimeError as exc:
                raise RuntimeError(f"{name}: {exc}") from exc

            for page_number, image in pdf_images:
                pages.append(PageImage(page_id, page_number, name, image))
                page_id += 1
        else:
            pages.append(PageImage(page_id, 1, name, load_image_file(uploaded_file)))
            page_id += 1

    return pages


def call_ollama_ocr(
    image: Image.Image,
    prompt: str,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
) -> str:
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image_to_base64(image)],
        "stream": False,
        "options": {
            "temperature": 0,
        },
    }

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            markdown = data.get("response", "").strip()
            if not markdown:
                raise RuntimeError("Ollama ha risposto senza contenuto OCR.")
            markdown = normalize_ocr_markdown(markdown)
            if looks_like_prompt_echo(markdown):
                raise RuntimeError("Il modello ha restituito le istruzioni del prompt invece del contenuto della pagina.")
            return markdown
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 8))

    raise RuntimeError(f"OCR fallito dopo {retries + 1} tentativi: {last_error}")


def build_combined_markdown(results: list[dict]) -> str:
    chunks = []
    for result in results:
        chunks.append(
            f"<!-- {result['source_name']} - pagina {result['page_number']} -->\n\n"
            f"{result['markdown'].strip()}"
        )
    return "\n\n---\n\n".join(chunks).strip()


def build_markdown_filename(input_filename: str, page_range: str = "") -> str:
    stem = Path(input_filename).stem or "ocr_result"
    clean_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "ocr_result"
    filename = clean_stem

    clean_range = page_range.strip()
    if clean_range:
        range_suffix = re.sub(r"\s+", "", clean_range)
        range_suffix = re.sub(r'[<>:"/\\|?*\x00-\x1f,]+', "_", range_suffix).strip("_")
        if range_suffix:
            filename = f"{filename}_p{range_suffix}"

    return f"{filename}.md"


def save_markdown_file(markdown: str, output_dir: str, filename: str) -> Path:
    clean_filename = Path(filename).name or "ocr_result.md"
    if not clean_filename.lower().endswith(".md"):
        clean_filename = f"{clean_filename}.md"

    target_dir = Path(output_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / clean_filename
    target_path.write_text(markdown, encoding="utf-8")
    return target_path


def count_unreadable_placeholders(results: list[dict]) -> int:
    return sum(
        len(re.findall(r"\[illeggibile\]", result["markdown"], flags=re.IGNORECASE))
        for result in results
    )


def build_ocr_summary(pages: list[PageImage], results: list[dict], errors: dict[int, str]) -> dict:
    processed_pages = [
        f"Pagina {result['page_number']}"
        for result in results
    ]
    error_items = [
        {
            "page": f"{pages[index].source_name} - pagina {pages[index].page_number}",
            "error": error,
        }
        for index, error in sorted(errors.items())
        if 0 <= index < len(pages)
    ]
    return {
        "total_pages": len(pages),
        "processed_count": len(results),
        "processed_pages": processed_pages,
        "error_count": len(error_items),
        "errors": error_items,
        "unreadable_count": count_unreadable_placeholders(results),
    }
