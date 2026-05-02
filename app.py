import os
from pathlib import Path

import streamlit as st

from ocr_core import (
    build_combined_markdown,
    build_markdown_filename,
    build_ocr_summary,
    call_ollama_ocr,
    save_markdown_file,
    uploaded_files_to_pages,
)


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-ocr")
DEFAULT_TIMEOUT = int(os.getenv("OCR_TIMEOUT_SECONDS", "180"))
DEFAULT_RETRIES = int(os.getenv("OCR_RETRIES", "2"))
OUTPUT_DIR = Path(__file__).resolve().parent / "out_md"

OCR_PROMPT = """Trascrivi questa pagina in Markdown pulito.

Regole obbligatorie:
- mantieni la struttura del documento;
- estrai testo, titoli, paragrafi e liste;
- converti le tabelle in tabelle Markdown;
- descrivi sinteticamente le figure quando presenti;
- non inventare contenuto non visibile;
- usa il placeholder "[illeggibile]" per parti non leggibili;
- restituisci solo Markdown, senza commenti introduttivi o conclusivi."""


st.set_page_config(page_title="OCR to Markdown", layout="wide", initial_sidebar_state="collapsed")


def reset_state() -> None:
    st.session_state.pages = []
    st.session_state.results = []
    st.session_state.errors = {}
    st.session_state.ocr_summary = None
    st.session_state.show_ocr_summary = False
    st.session_state.output_filename = None
    st.session_state.saved_path = None
    st.session_state.save_error = None


def initialize_state() -> None:
    if "pages" not in st.session_state:
        reset_state()
    if "ocr_summary" not in st.session_state:
        st.session_state.ocr_summary = None
    if "show_ocr_summary" not in st.session_state:
        st.session_state.show_ocr_summary = False
    if "output_filename" not in st.session_state:
        st.session_state.output_filename = None
    if "saved_path" not in st.session_state:
        st.session_state.saved_path = None
    if "save_error" not in st.session_state:
        st.session_state.save_error = None


def prepare_uploaded_pages(uploaded_files, dpi: int, page_range: str) -> bool:
    if not uploaded_files:
        st.warning("Carica almeno un file.")
        return False

    with st.spinner("Conversione documenti in immagini..."):
        try:
            st.session_state.pages = uploaded_files_to_pages(uploaded_files, dpi, page_range)
            st.session_state.output_filename = build_markdown_filename(uploaded_files[0].name, page_range)
        except RuntimeError as exc:
            st.error(str(exc))
            return False

    st.success(f"Pronte {len(st.session_state.pages)} pagine.")
    return True


def get_output_filename(results: list[dict]) -> str:
    if st.session_state.output_filename:
        return st.session_state.output_filename
    if results:
        return build_markdown_filename(results[0]["source_name"])
    return "ocr_result.md"


def save_results_automatically() -> None:
    results = st.session_state.results
    if not results:
        return

    combined = build_combined_markdown(results)
    output_filename = get_output_filename(results)
    try:
        st.session_state.saved_path = save_markdown_file(combined, str(OUTPUT_DIR), output_filename)
        st.session_state.save_error = None
    except OSError as exc:
        st.session_state.saved_path = None
        st.session_state.save_error = str(exc)


def run_ocr_for_pages(prompt: str, model: str, base_url: str, timeout: int, retries: int) -> None:
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
    save_results_automatically()
    st.session_state.ocr_summary = build_ocr_summary(
        st.session_state.pages,
        st.session_state.results,
        st.session_state.errors,
    )
    st.session_state.show_ocr_summary = True


def render_sidebar() -> dict:
    with st.sidebar:
        with st.expander("Parametri OCR", expanded=False):
            return {
                "base_url": st.text_input("Ollama base URL", value=OLLAMA_BASE_URL),
                "model": st.text_input("Modello", value=OLLAMA_MODEL),
                "dpi": st.slider("Risoluzione PDF", min_value=120, max_value=300, value=200, step=20),
                "page_range": st.text_input(
                    "Pagine PDF da convertire",
                    value="",
                    placeholder="Tutte, oppure es. 1-3, 5, 8-10",
                    help=(
                        "Lascia vuoto per convertire tutte le pagine. Il filtro vale per i PDF; "
                        "le immagini caricate singolarmente vengono incluse sempre."
                    ),
                ),
                "timeout": st.number_input(
                    "Timeout per pagina (secondi)",
                    min_value=30,
                    max_value=900,
                    value=DEFAULT_TIMEOUT,
                ),
                "retries": st.number_input("Retry per pagina", min_value=0, max_value=5, value=DEFAULT_RETRIES),
                "prompt": st.text_area("Prompt OCR", value=OCR_PROMPT, height=260),
            }


@st.dialog("Esito elaborazione OCR")
def show_ocr_summary_dialog() -> None:
    summary = st.session_state.ocr_summary
    if not summary:
        st.info("Nessun riepilogo disponibile.")
        return

    st.write(f"Pagine selezionate: **{summary['total_pages']}**")
    st.write(f"Pagine elaborate correttamente: **{summary['processed_count']}**")
    st.write(f"Errori: **{summary['error_count']}**")
    st.write(f"Elementi `[illeggibile]`: **{summary['unreadable_count']}**")

    if summary["processed_pages"]:
        with st.expander("Pagine elaborate", expanded=True):
            for page_label in summary["processed_pages"]:
                st.write(f"- {page_label}")

    if summary["errors"]:
        with st.expander("Errori", expanded=True):
            for item in summary["errors"]:
                st.error(f"{item['page']}: {item['error']}")

    if st.button("Chiudi", width="stretch"):
        st.session_state.show_ocr_summary = False
        st.rerun()


def render_upload_actions(uploaded_files, config: dict) -> None:
    actions = st.columns([1, 1, 4])
    with actions[0]:
        prepare = st.button("Prepara pagine", type="secondary", width="stretch")
    with actions[1]:
        run_ocr = st.button("Avvia OCR", type="primary", width="stretch")

    if prepare:
        reset_state()
        prepare_uploaded_pages(uploaded_files, config["dpi"], config["page_range"])

    if run_ocr:
        if not st.session_state.pages:
            prepared = prepare_uploaded_pages(uploaded_files, config["dpi"], config["page_range"])
            if not prepared:
                st.stop()

        run_ocr_for_pages(
            prompt=config["prompt"],
            model=config["model"],
            base_url=config["base_url"],
            timeout=int(config["timeout"]),
            retries=int(config["retries"]),
        )


def render_page_comparison() -> None:
    pages = st.session_state.pages
    results = st.session_state.results
    errors = st.session_state.errors

    if not pages:
        return

    st.divider()
    page_labels = [
        f"{index + 1}. {page.source_name} - pagina {page.page_number}"
        for index, page in enumerate(pages)
    ]
    selector_col, download_col = st.columns([3, 1])
    with selector_col:
        selected = st.selectbox("Pagina da confrontare", options=range(len(pages)), format_func=lambda i: page_labels[i])
    with download_col:
        if results:
            output_filename = get_output_filename(results)
            st.download_button(
                "Scarica Markdown",
                data=build_combined_markdown(results).encode("utf-8"),
                file_name=Path(output_filename).name or "ocr_result.md",
                mime="text/markdown",
                width="stretch",
            )
    if st.session_state.save_error:
        st.error(f"Impossibile salvare automaticamente il Markdown: {st.session_state.save_error}")
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
            save_results_automatically()
            with st.expander("Anteprima renderizzata", expanded=False):
                st.markdown(edited)
        else:
            st.info("Esegui l'OCR per vedere il Markdown di questa pagina.")


def main() -> None:
    initialize_state()

    st.title("OCR to Markdown")
    config = render_sidebar()
    uploaded_files = st.file_uploader(
        "Carica il documento da trasformare in Markdown (PDF scansionati, PDF sporchi, immagini ...)",
        type=["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"],
        accept_multiple_files=True,
    )

    render_upload_actions(uploaded_files, config)

    if st.session_state.show_ocr_summary:
        show_ocr_summary_dialog()

    render_page_comparison()


if __name__ == "__main__":
    main()
