# digitizeMyBook

Lokale Web-App: Scans und PDFs per GPU-OCR in **EPUB3** (reflowbar, mit Kapiteln) oder ein **durchsuchbares PDF** verwandeln. Nichts verlässt den Rechner.

## Voraussetzungen

- Linux, Python 3.12, Node 20+
- NVIDIA-Treiber mit CUDA 12.9+ (getestet: RTX 5060 Ti, Treiber 580 / CUDA 13.0)
- Optional: `zenity` für den Ordnerdialog (`sudo apt install zenity`)

## Start

**Normal (empfohlen)** – beendet alte Prozesse auf Port 8765/5173, baut das UI und startet das Backend:

```bash
chmod +x start.sh
./start.sh
```

Browser: [http://127.0.0.1:8765](http://127.0.0.1:8765)

**Entwicklung** (Backend + Vite mit Hot-Reload):

```bash
./start.sh --dev
```

Browser: [http://127.0.0.1:5173](http://127.0.0.1:5173)

**Erstes Mal / fehlende Abhängigkeiten** (venv, Paddle, npm):

```bash
chmod +x run.sh
./run.sh
```

(`run.sh` richtet alles ein und startet den Dev-Stack; für den Alltag reicht `./start.sh`.)

Die UI spricht mit dem Backend auf Port **8765**. Jobs liegen unter `~/.local/share/digitizeMyBook/jobs/`. Beim ersten OCR-Lauf lädt PaddleOCR Modelle nach `~/.paddlex/` (einmalig, einige hundert MB).

## GPU-Check

```bash
source .venv/bin/activate
PYTHONPATH=backend python -m app.cli check-gpu
```

PaddlePaddle kommt von Baidus cu129-Index, PaddleOCR separat:

```bash
source .venv/bin/activate
python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
python -m pip install -U "paddleocr[doc-parser]"
python -m pip install -r backend/requirements.txt
```

## Bedienung

1. Ordner, Bilder oder PDFs wählen (Pfad bleibt lokal, kein Upload).
2. Optional **Testseite** – prüft OCR an Seite 1.
3. **Schnell** (PP-StructureV3) oder **Qualität** (PaddleOCR-VL).
4. Kapitel umbenennen, mergen oder teilen.
5. EPUB und/oder PDF exportieren.

## CLI

```bash
PYTHONPATH=backend python -m app.cli convert --source /pfad/zum/buch --mode fast --pdf
PYTHONPATH=backend python -m app.cli convert --source scan.pdf --test-page
```
