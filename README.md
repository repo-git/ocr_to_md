# OCR to Markdown

**Versione:** `1.0`

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
- Salvataggio automatico del Markdown nella cartella `out_md` e download dal browser.
- Normalizzazione automatica delle tabelle HTML restituite dal modello in tabelle Markdown.

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

## Procedura batch

La procedura batch converte in Markdown tutti i file supportati presenti in una cartella, usando le stesse funzioni OCR gia disponibili nel progetto. I file `.md` vengono salvati nella cartella `out_md`, salvo diversa indicazione.

Per avviare la conversione:

```powershell
.\.venv\Scripts\python.exe batch_ocr.py "C:\percorso\cartella_input"
```

Per scegliere una cartella di output diversa:

```powershell
.\.venv\Scripts\python.exe batch_ocr.py "C:\percorso\cartella_input" --output-dir "C:\percorso\output_md"
```

Per includere anche le sottocartelle:

```powershell
.\.venv\Scripts\python.exe batch_ocr.py "C:\percorso\cartella_input" --recursive
```

I PDF vengono elaborati a blocchi sovrapposti per ridurre gli errori nelle lavorazioni lunghe. Con il valore predefinito, un PDF di 25 pagine viene suddiviso in:

```text
1-8, 8-16, 16-25
```

Le pagine sovrapposte vengono rielaborate, ma nel Markdown finale ogni pagina viene scritta una sola volta. Dopo ogni blocco completato il file `.md` viene aggiornato, cosi il progresso resta salvato anche durante elaborazioni lunghe.

A fine elaborazione viene creato nella cartella di output un file di log con nome basato sul timestamp, per esempio `20260503_205819.log`. Il log riporta per ogni file convertito il Markdown prodotto, eventuali errori con numero pagina e messaggio, e l'elenco delle pagine che contengono parti `[illeggibile]`.

Opzioni principali:

| Opzione | Default | Descrizione |
| --- | --- | --- |
| `--output-dir` | `out_md` | Cartella in cui salvare Markdown e log |
| `--recursive` | disattivo | Include anche le sottocartelle |
| `--base-url` | `OLLAMA_BASE_URL` o `http://localhost:11434` | URL del server Ollama |
| `--model` | `OLLAMA_MODEL` o `glm-ocr` | Modello OCR da usare |
| `--dpi` | `200` | Risoluzione di rendering dei PDF |
| `--block-size` | `8` | Dimensione dei blocchi PDF sovrapposti |
| `--timeout` | `OCR_TIMEOUT_SECONDS` o `180` | Timeout per pagina in secondi |
| `--retries` | `OCR_RETRIES` o `2` | Numero di retry per pagina |

## Struttura del progetto

| File | Ruolo |
| --- | --- |
| `app.py` | Interfaccia Streamlit, stato UI, confronto pagine e controlli export |
| `batch_ocr.py` | Procedura batch da cartella con blocchi PDF sovrapposti e log finale |
| `ocr_core.py` | Conversione PDF/immagini, parsing range pagine, chiamate Ollama, riepiloghi e salvataggio Markdown |
| `.env.example` | Esempio di configurazione ambiente |
| `requirements.txt` | Dipendenze Python |

## Configurazione

Variabili ambiente supportate:

| Variabile | Default | Descrizione |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | URL del server Ollama locale |
| `OLLAMA_MODEL` | `glm-ocr` | Nome del modello OCR da usare |
| `OCR_TIMEOUT_SECONDS` | `180` | Timeout massimo per pagina |
| `OCR_RETRIES` | `2` | Numero di retry per pagina |

Esempio `.env`:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=glm-ocr:latest
OCR_TIMEOUT_SECONDS=180
OCR_RETRIES=2
```

## Flusso di lavoro

1. Carica uno o piu PDF o immagini.
2. Indica eventualmente le pagine PDF da convertire, per esempio `1-3, 5, 8-10`.
3. Prepara le pagine: i PDF vengono renderizzati pagina per pagina.
4. Avvia OCR: ogni pagina viene inviata a GLM-OCR tramite Ollama.
5. Confronta originale e Markdown nella vista affiancata.
6. Correggi il Markdown se serve.
7. Scarica il risultato dal browser se ti serve una copia manuale.

Il file Markdown viene salvato automaticamente nella cartella `out_md` e usa lo stesso nome del file di input con estensione `.md`.
Per conversioni parziali viene aggiunto il suffisso `_p` seguito dal range pagine, per esempio `documento_p1-3_5.md`.

## Note

L'app usa l'endpoint nativo Ollama `/api/generate` con `stream: false` e invia ogni pagina come immagine PNG codificata in base64.

Il prompt OCR chiede al modello di non inventare contenuti non visibili e di usare `[illeggibile]` per le parti non leggibili.
