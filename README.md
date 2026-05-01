# OCR to Markdown

**Versione:** `0.2`

Webapp Streamlit per trasformare PDF scansionati, PDF rumorosi e immagini in Markdown pulito usando **GLM-OCR** esposto tramite **Ollama locale**.

L'app converte ogni pagina in immagine, la invia al modello OCR e mostra una vista affiancata per confrontare il documento originale con il Markdown generato.

## Funzionalita

- Upload di PDF e immagini multiple.
- Conversione dei PDF pagina per pagina in immagini.
- Selezione opzionale delle pagine PDF da convertire.
- OCR locale tramite endpoint Ollama `/api/generate`.
- Prompt orientato a Markdown strutturato.
- Estrazione di titoli, paragrafi, liste e tabelle Markdown.
- Descrizione sintetica delle figure quando presenti.
- Gestione di timeout, retry ed errori per pagina.
- Confronto affiancato tra originale e risultato OCR.
- Editing manuale del Markdown generato.
- Salvataggio del Markdown su disco o download dal browser.

## Requisiti

- Python 3.10+
- Ollama in esecuzione su `http://localhost:11434`
- Modello GLM-OCR installato localmente

Installa il modello:

```powershell
ollama pull glm-ocr:latest
```

Se il modello locale si chiama `glm-ocr:latest`, imposta `OLLAMA_MODEL=glm-ocr:latest`.

## Installazione

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Avvio

```powershell
streamlit run app.py
```

Poi apri l'indirizzo mostrato da Streamlit, di solito:

```text
http://localhost:8501
```

## Configurazione

Variabili ambiente supportate:

| Variabile | Default | Descrizione |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | URL del server Ollama locale |
| `OLLAMA_MODEL` | `glm-ocr` | Nome del modello OCR da usare |
| `OCR_TIMEOUT_SECONDS` | `180` | Timeout massimo per pagina |
| `OCR_RETRIES` | `2` | Numero di retry per pagina |
| `MARKDOWN_OUTPUT_DIR` | `outputs` | Directory predefinita per salvare il Markdown |

Esempio `.env`:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=glm-ocr:latest
OCR_TIMEOUT_SECONDS=180
OCR_RETRIES=2
MARKDOWN_OUTPUT_DIR=outputs
```

## Flusso di lavoro

1. Carica uno o piu PDF o immagini.
2. Indica eventualmente le pagine PDF da convertire, per esempio `1-3, 5, 8-10`.
3. Prepara le pagine: i PDF vengono renderizzati pagina per pagina.
4. Avvia OCR: ogni pagina viene inviata a GLM-OCR tramite Ollama.
5. Confronta originale e Markdown nella vista affiancata.
6. Correggi il Markdown se serve.
7. Salva il risultato su disco o scaricalo dal browser.

## Note

L'app usa l'endpoint nativo Ollama `/api/generate` con `stream: false` e invia ogni pagina come immagine PNG codificata in base64.

Il prompt OCR chiede al modello di non inventare contenuti non visibili e di usare `[illeggibile]` per le parti non leggibili.
