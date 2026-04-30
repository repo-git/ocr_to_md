# OCR to Markdown

Webapp Streamlit per trasformare PDF scansionati, PDF rumorosi e immagini in Markdown pulito usando GLM-OCR esposto da Ollama in locale.

## Requisiti

- Python 3.10+
- Ollama in esecuzione su `http://localhost:11434`
- Modello GLM-OCR installato, per esempio:

```powershell
ollama pull glm-ocr:latest
```

Se il modello locale si chiama `glm-ocr:latest`, imposta `OLLAMA_MODEL=glm-ocr:latest`.

## Avvio

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Configurazione

Variabili ambiente supportate:

- `OLLAMA_BASE_URL`, default `http://localhost:11434`
- `OLLAMA_MODEL`, default `glm-ocr`
- `OCR_TIMEOUT_SECONDS`, default `180`
- `OCR_RETRIES`, default `2`

L'app usa l'endpoint nativo Ollama `/api/generate` con `stream: false` e invia ogni pagina come immagine PNG base64.

## Flusso

1. Carica uno o più PDF o immagini.
2. Prepara le pagine: i PDF vengono renderizzati pagina per pagina come immagini.
3. Avvia OCR: ogni pagina viene inviata a GLM-OCR.
4. Confronta originale e Markdown pagina per pagina.
5. Modifica il Markdown se serve e scarica il file finale.
