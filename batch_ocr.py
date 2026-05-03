import argparse
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import fitz
from PIL import Image

from ocr_core import (
    PageImage,
    build_combined_markdown,
    build_markdown_filename,
    call_ollama_ocr,
    pdf_to_images,
    save_markdown_file,
)


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-ocr")
DEFAULT_TIMEOUT = int(os.getenv("OCR_TIMEOUT_SECONDS", "180"))
DEFAULT_RETRIES = int(os.getenv("OCR_RETRIES", "2"))
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "out_md"
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
OCR_PROMPT = """Trascrivi questa pagina in Markdown pulito.

Regole obbligatorie:
- mantieni la struttura del documento;
- estrai testo, titoli, paragrafi e liste;
- converti le tabelle in tabelle Markdown;
- non usare HTML: non restituire tag <table>, <tr>, <td>, <th>, <ul>, <ol> o <li>;
- descrivi sinteticamente le figure quando presenti;
- non inventare contenuto non visibile;
- usa il placeholder "[illeggibile]" per parti non leggibili;
- restituisci solo Markdown, senza commenti introduttivi o conclusivi."""


@dataclass
class LocalPdfFile:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def type(self) -> str:
        return "application/pdf"

    def getvalue(self) -> bytes:
        return self.path.read_bytes()


@dataclass
class BatchResult:
    source: Path
    output: Path | None
    processed_pages: int
    errors: dict[int, str]
    unreadable_pages: list[int]


def iter_input_files(input_dir: Path, recursive: bool) -> list[Path]:
    paths = input_dir.rglob("*") if recursive else input_dir.iterdir()
    return sorted(
        path
        for path in paths
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def get_pdf_page_count(path: Path) -> int:
    try:
        with fitz.open(str(path)) as document:
            return document.page_count
    except fitz.FileDataError as exc:
        raise RuntimeError("PDF non leggibile o danneggiato.") from exc
    except RuntimeError as exc:
        raise RuntimeError("Errore durante la lettura del PDF.") from exc


def build_overlapping_page_ranges(total_pages: int, block_size: int = 8) -> list[tuple[int, int]]:
    if total_pages < 1:
        return []
    if block_size < 1:
        raise ValueError("La dimensione del blocco deve essere almeno 1.")

    ranges: list[tuple[int, int]] = []
    start = 1
    while start <= total_pages:
        end = start + block_size - 1 if not ranges else start + block_size
        end = min(total_pages, end)
        if total_pages - end <= 1:
            end = total_pages
        ranges.append((start, end))
        if end >= total_pages:
            break
        start = end
    return ranges


def render_pdf_pages(path: Path, dpi: int, start: int, end: int, first_page_id: int) -> list[PageImage]:
    images = pdf_to_images(LocalPdfFile(path), dpi=dpi, page_range=f"{start}-{end}")
    return [
        PageImage(first_page_id + index, page_number, path.name, image)
        for index, (page_number, image) in enumerate(images)
    ]


def render_image_page(path: Path) -> PageImage:
    with Image.open(path) as source_image:
        image = source_image.convert("RGB")
    return PageImage(0, 1, path.name, image)


def ocr_page(
    page: PageImage,
    prompt: str,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
) -> str:
    return call_ollama_ocr(
        image=page.image,
        prompt=prompt,
        model=model,
        base_url=base_url,
        timeout=timeout,
        retries=retries,
    )


def save_progress(results_by_page: dict[int, dict], output_dir: Path, source_name: str) -> Path | None:
    if not results_by_page:
        return None

    ordered_results = [
        results_by_page[page_number]
        for page_number in sorted(results_by_page)
    ]
    markdown = build_combined_markdown(ordered_results)
    return save_markdown_file(markdown, str(output_dir), build_markdown_filename(source_name))


def collect_unreadable_pages(results_by_page: dict[int, dict]) -> list[int]:
    return [
        page_number
        for page_number, result in sorted(results_by_page.items())
        if "[illeggibile]" in result["markdown"].lower()
    ]


def process_pages(
    pages: list[PageImage],
    results_by_page: dict[int, dict],
    errors_by_page: dict[int, str],
    prompt: str,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
) -> None:
    for page in pages:
        try:
            markdown = ocr_page(page, prompt, model, base_url, timeout, retries)
        except RuntimeError as exc:
            if page.page_number not in results_by_page:
                errors_by_page[page.page_number] = str(exc)
            print(f"    Pagina {page.page_number}: ERRORE - {exc}")
            continue

        if page.page_number not in results_by_page:
            results_by_page[page.page_number] = {
                "page_id": page.page_id,
                "source_name": page.source_name,
                "page_number": page.page_number,
                "markdown": markdown,
            }
        errors_by_page.pop(page.page_number, None)
        print(f"    Pagina {page.page_number}: OK")


def process_pdf_file(
    path: Path,
    output_dir: Path,
    dpi: int,
    block_size: int,
    prompt: str,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
) -> BatchResult:
    total_pages = get_pdf_page_count(path)
    page_ranges = build_overlapping_page_ranges(total_pages, block_size=block_size)
    results_by_page: dict[int, dict] = {}
    errors_by_page: dict[int, str] = {}
    output_path: Path | None = None

    print(f"\n{path.name}: {total_pages} pagine, {len(page_ranges)} blocchi")
    for chunk_index, (start, end) in enumerate(page_ranges, start=1):
        print(f"  Blocco {chunk_index}/{len(page_ranges)}: pagine {start}-{end}")
        try:
            pages = render_pdf_pages(path, dpi, start, end, first_page_id=start - 1)
        except RuntimeError as exc:
            for page_number in range(start, end + 1):
                if page_number not in results_by_page:
                    errors_by_page[page_number] = str(exc)
            print(f"    Conversione blocco fallita: {exc}")
            continue

        process_pages(pages, results_by_page, errors_by_page, prompt, model, base_url, timeout, retries)
        output_path = save_progress(results_by_page, output_dir, path.name)

    return BatchResult(
        source=path,
        output=output_path,
        processed_pages=len(results_by_page),
        errors=errors_by_page,
        unreadable_pages=collect_unreadable_pages(results_by_page),
    )


def process_image_file(
    path: Path,
    output_dir: Path,
    prompt: str,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
) -> BatchResult:
    results_by_page: dict[int, dict] = {}
    errors_by_page: dict[int, str] = {}
    output_path: Path | None = None

    print(f"\n{path.name}: immagine singola")
    try:
        page = render_image_page(path)
        process_pages([page], results_by_page, errors_by_page, prompt, model, base_url, timeout, retries)
        output_path = save_progress(results_by_page, output_dir, path.name)
    except (OSError, RuntimeError) as exc:
        errors_by_page[1] = str(exc)
        print(f"    Pagina 1: ERRORE - {exc}")

    return BatchResult(
        source=path,
        output=output_path,
        processed_pages=len(results_by_page),
        errors=errors_by_page,
        unreadable_pages=collect_unreadable_pages(results_by_page),
    )


def process_file(
    path: Path,
    output_dir: Path,
    dpi: int,
    block_size: int,
    prompt: str,
    model: str,
    base_url: str,
    timeout: int,
    retries: int,
) -> BatchResult:
    if path.suffix.lower() == ".pdf":
        return process_pdf_file(path, output_dir, dpi, block_size, prompt, model, base_url, timeout, retries)
    return process_image_file(path, output_dir, prompt, model, base_url, timeout, retries)


def print_final_summary(results: list[BatchResult]) -> None:
    print("\nRiepilogo batch")
    for result in results:
        output = str(result.output) if result.output else "non salvato"
        print(f"- {result.source.name}: {result.processed_pages} pagine salvate -> {output}")
        if result.errors:
            errors = ", ".join(
                f"{'file' if page == 0 else page}: {error}"
                for page, error in sorted(result.errors.items())
            )
            print(f"  Errori: {errors}")
        if result.unreadable_pages:
            unreadable = ", ".join(str(page) for page in result.unreadable_pages)
            print(f"  Parti non leggibili nelle pagine: {unreadable}")


def format_page_errors(errors: dict[int, str]) -> list[str]:
    if not errors:
        return ["  Errori: nessuno"]

    lines = ["  Errori:"]
    for page, error in sorted(errors.items()):
        page_label = "file" if page == 0 else f"pagina {page}"
        lines.append(f"    - {page_label}: {error}")
    return lines


def format_unreadable_pages(unreadable_pages: list[int]) -> str:
    if not unreadable_pages:
        return "  Pagine con parti illegibili: nessuna"
    pages = ", ".join(str(page) for page in unreadable_pages)
    return f"  Pagine con parti illegibili: {pages}"


def write_batch_log(results: list[BatchResult], output_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = output_dir / f"{timestamp}.log"
    lines = [
        f"Log elaborazione batch OCR - {timestamp}",
        "",
    ]

    for result in results:
        output = str(result.output) if result.output else "non salvato"
        lines.extend(
            [
                f"File: {result.source}",
                f"  Markdown: {output}",
                f"  Pagine convertite: {result.processed_pages}",
            ]
        )
        lines.extend(format_page_errors(result.errors))
        lines.append(format_unreadable_pages(result.unreadable_pages))
        lines.append("")

    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return log_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Converte in batch PDF e immagini in file Markdown.")
    parser.add_argument("input_dir", type=Path, help="Cartella contenente i file da convertire.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Cartella di destinazione dei file .md.")
    parser.add_argument("--recursive", action="store_true", help="Legge anche le sottocartelle.")
    parser.add_argument("--base-url", default=OLLAMA_BASE_URL, help="URL del server Ollama.")
    parser.add_argument("--model", default=OLLAMA_MODEL, help="Modello OCR da usare.")
    parser.add_argument("--dpi", type=int, default=200, help="Risoluzione usata per renderizzare i PDF.")
    parser.add_argument("--block-size", type=int, default=8, help="Dimensione del blocco PDF con sovrapposizione sul confine.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout per pagina in secondi.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retry per pagina.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not input_dir.is_dir():
        raise SystemExit(f"La cartella di input non esiste: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    files = iter_input_files(input_dir, recursive=args.recursive)
    if not files:
        raise SystemExit("Nessun file supportato trovato nella cartella di input.")

    results = []
    for path in files:
        try:
            result = process_file(
                path=path,
                output_dir=output_dir,
                dpi=args.dpi,
                block_size=args.block_size,
                prompt=OCR_PROMPT,
                model=args.model,
                base_url=args.base_url,
                timeout=args.timeout,
                retries=args.retries,
            )
        except RuntimeError as exc:
            print(f"\n{path.name}: ERRORE - {exc}")
            result = BatchResult(path, None, 0, {0: str(exc)}, [])
        results.append(result)

    print_final_summary(results)
    log_path = write_batch_log(results, output_dir)
    print(f"\nLog salvato in: {log_path}")


if __name__ == "__main__":
    main()
