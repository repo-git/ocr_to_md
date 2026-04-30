import base64
import os
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

import fitz
import requests
import streamlit as st
from PIL import Image

fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-ocr")
DEFAULT_TIMEOUT = int(os.getenv("OCR_TIMEOUT_SECONDS", "180"))
DEFAULT_RETRIES = int(os.getenv("OCR_RETRIES", "2"))
DEFAULT_OUTPUT_DIR = os.getenv("MARKDOWN_OUTPUT_DIR", "outputs")

OCR_PROMPT = """Trascrivi questa pagina in Markdown pulito.

Regole obbligatorie:
- mantieni la struttura del documento;
- estrai testo, titoli, paragrafi e liste;
- converti le tabelle in tabelle Markdown;
- descrivi sinteticamente le figure quando presenti;
- non inventare contenuto non visibile;
- usa il placeholder "[illeggibile]" per parti non leggibili;
- restituisci solo Markdown, senza commenti introduttivi o conclusivi."""


@dataclass
class PageImage:
    page_id: int
    page_number: int
    source_name: str
    image: Image.Image


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def image_to_base64(image: Image.Image) -> str:
    return base64.b64encode(image_to_png_bytes(image)).decode("ascii")


def load_image_file(file) -> Image.Image:
    image = Image.open(file)
    return image.convert("RGB")


def pdf_to_images(file, dpi: int) -> list[Image.Image]:
    pdf_bytes = file.getvalue()
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    images: list[Image.Image] = []

    try:
        fitz.TOOLS.reset_mupdf_warnings()
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            for page in document:
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")
                images.append(image)
    except fitz.FileDataError as exc:
        raise RuntimeError("PDF non leggibile o danneggiato.") from exc
    except fitz.FileNotFoundError as exc:
        raise RuntimeError("PDF non trovato.") from exc
    except RuntimeError as exc:
        warnings = fitz.TOOLS.mupdf_warnings().strip()
        details = f" Dettagli MuPDF: {warnings}" if warnings else ""
        raise RuntimeError(f"Errore durante la conversione del PDF.{details}") from exc

    return images


def uploaded_files_to_pages(files: Iterable, dpi: int) -> list[PageImage]:
    pages: list[PageImage] = []
    page_id = 0

    for uploaded_file in files:
        name = uploaded_file.name
        content_type = uploaded_file.type or ""
        extension = os.path.splitext(name)[1].lower()

        if content_type == "application/pdf" or extension == ".pdf":
            try:
                pdf_images = pdf_to_images(uploaded_file, dpi)
            except RuntimeError as exc:
                raise RuntimeError(f"{name}: {exc}") from exc

            for index, image in enumerate(pdf_images, start=1):
                pages.append(PageImage(page_id, index, name, image))
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


def save_markdown_file(markdown: str, output_dir: str, filename: str) -> Path:
    clean_filename = Path(filename).name or "ocr_result.md"
    if not clean_filename.lower().endswith(".md"):
        clean_filename = f"{clean_filename}.md"

    target_dir = Path(output_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / clean_filename
    target_path.write_text(markdown, encoding="utf-8")
    return target_path


def reset_state() -> None:
    st.session_state.pages = []
    st.session_state.results = []
    st.session_state.errors = {}


st.set_page_config(page_title="OCR to Markdown", layout="wide", initial_sidebar_state="collapsed")

if "pages" not in st.session_state:
    reset_state()

st.title("OCR to Markdown")

with st.sidebar:
    with st.expander("Parametri OCR", expanded=False):
        base_url = st.text_input("Ollama base URL", value=OLLAMA_BASE_URL)
        model = st.text_input("Modello", value=OLLAMA_MODEL)
        dpi = st.slider("Risoluzione PDF", min_value=120, max_value=300, value=200, step=20)
        timeout = st.number_input("Timeout per pagina (secondi)", min_value=30, max_value=900, value=DEFAULT_TIMEOUT)
        retries = st.number_input("Retry per pagina", min_value=0, max_value=5, value=DEFAULT_RETRIES)
        output_dir = st.text_input("Directory output Markdown", value=DEFAULT_OUTPUT_DIR)
        output_filename = st.text_input("Nome file Markdown", value="ocr_result.md")
        prompt = st.text_area("Prompt OCR", value=OCR_PROMPT, height=260)

uploaded_files = st.file_uploader(
    "Carica il documento da trasformare in Markdown (PDF scansionati, PDF sporchi, immagini ...)",
    type=["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"],
    accept_multiple_files=True,
)

actions = st.columns([1, 1, 4])
with actions[0]:
    prepare = st.button("Prepara pagine", type="secondary", width="stretch")
with actions[1]:
    run_ocr = st.button("Avvia OCR", type="primary", width="stretch")

if prepare:
    reset_state()
    if uploaded_files:
        with st.spinner("Conversione documenti in immagini..."):
            try:
                st.session_state.pages = uploaded_files_to_pages(uploaded_files, dpi)
                st.success(f"Pronte {len(st.session_state.pages)} pagine.")
            except RuntimeError as exc:
                st.error(str(exc))
    else:
        st.warning("Carica almeno un file.")

if run_ocr:
    if not st.session_state.pages:
        if uploaded_files:
            with st.spinner("Conversione documenti in immagini..."):
                try:
                    st.session_state.pages = uploaded_files_to_pages(uploaded_files, dpi)
                except RuntimeError as exc:
                    st.error(str(exc))
                    st.stop()
        else:
            st.warning("Carica almeno un file.")
            st.stop()

    st.session_state.results = []
    st.session_state.errors = {}
    progress = st.progress(0)
    status = st.empty()

    total = len(st.session_state.pages)
    for index, page in enumerate(st.session_state.pages, start=1):
        status.info(f"OCR pagina {index}/{total}: {page.source_name}, pagina {page.page_number}")
        try:
            markdown = call_ollama_ocr(
                image=page.image,
                prompt=prompt,
                model=model,
                base_url=base_url,
                timeout=int(timeout),
                retries=int(retries),
            )
            st.session_state.results.append(
                {
                    "page_id": page.page_id,
                    "source_name": page.source_name,
                    "page_number": page.page_number,
                    "markdown": markdown,
                }
            )
        except RuntimeError as exc:
            st.session_state.errors[index - 1] = str(exc)
        progress.progress(index / total)

    status.success("OCR completato.")

pages = st.session_state.pages
results = st.session_state.results
errors = st.session_state.errors

if pages:
    st.divider()
    page_labels = [
        f"{index + 1}. {page.source_name} - pagina {page.page_number}"
        for index, page in enumerate(pages)
    ]
    selected = st.selectbox("Pagina da confrontare", options=range(len(pages)), format_func=lambda i: page_labels[i])
    page = pages[selected]

    result_by_page_id = {
        result["page_id"]: result["markdown"]
        for result in results
    }
    selected_markdown = result_by_page_id.get(page.page_id, "")

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Originale")
        st.image(page.image, width="stretch")

    with right:
        st.subheader("Markdown OCR")
        if selected in errors:
            st.error(errors[selected])
        elif selected_markdown:
            edited = st.text_area("Risultato modificabile", value=selected_markdown, height=720, key=f"md_{selected}")
            for result in results:
                if result["page_id"] == page.page_id:
                    result["markdown"] = edited
                    break
            with st.expander("Anteprima renderizzata", expanded=False):
                st.markdown(edited)
        else:
            st.info("Esegui l'OCR per vedere il Markdown di questa pagina.")

if results:
    st.divider()
    combined = build_combined_markdown(results)
    save_col, download_col = st.columns([1, 1])
    with save_col:
        if st.button("Salva Markdown su disco", type="secondary", width="stretch"):
            try:
                saved_path = save_markdown_file(combined, output_dir, output_filename)
                st.success(f"Markdown salvato in: {saved_path}")
            except OSError as exc:
                st.error(f"Impossibile salvare il Markdown: {exc}")
    with download_col:
        st.download_button(
            "Scarica Markdown completo",
            data=combined.encode("utf-8"),
            file_name=Path(output_filename).name or "ocr_result.md",
            mime="text/markdown",
            width="stretch",
        )
