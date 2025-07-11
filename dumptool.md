# 🧠 Civitai Metadata Dumper (`dumptool.py`)

This tool downloads metadata (JSON files) for AI models hosted on [Civitai.com](https://civitai.com) using their public API. It's designed for archival purposes, metadata analysis, or powering offline tools like model managers and search engines.

---

## 🚀 Features

- Downloads full model metadata in organized folder blocks.
- Retries failed requests (e.g. network issues, 429s).
- Upward crawling to find newly uploaded models.
- Rechecks previously crawled but unavailable IDs.
- Optional colored output to highlight status and errors.
- Auto-generates logs to track completed, failed, and skipped IDs.
- Intelligent validation of downloaded JSON to detect corrupted files.
- Sorted folder structure for easy archiving (e.g., `00000000/00000001.json`).

---

## ⚙️ Requirements

- Python 3.8+
- `requests` library (install with `pip install requests`)

---

## 🧾 Usage

### Basic Syntax

python dumptool.py [options]

### Options

| Argument               | Description                                                                 |
|------------------------|-----------------------------------------------------------------------------|
| --start <int>          | Starting model ID to begin downloading.                                     |
| --end <int>            | Ending model ID to stop downloading.                                        |
| --retry                | Retry failed downloads from the `errors.txt` log.                           |
| --threads <int>        | Number of concurrent threads. Default: 5.                                   |
| -o, --out <folder>     | Output folder for JSON + logs. Default: `civitai-meta`.                     |
| --color                | Enable colored output (default: on).                                        |
| --no-color             | Disable colored output for basic terminals or scripting.                    |
| --crawl                | Auto-crawl upwards from the highest model ID to discover new ones.          |
| --recheck-crawled      | Recheck IDs saved from prior crawl attempts (`crawled.txt`).                |

---

## 📂 Output Files

Each JSON file is saved to a folder based on its block (e.g., ID `12345678` goes in `12340000/12345678.json`).

Tracking files created automatically:

| File Name         | Purpose                                           |
|-------------------|---------------------------------------------------|
| downloaded.txt    | List of successful model downloads                |
| notfound.txt      | List of 404 (not found) responses                 |
| errors.txt        | All non-404 errors (e.g., 429, timeout)           |
| crawled.txt       | Model IDs discovered during upward crawling       |
| missing.txt       | Files that exist on disk but were not logged yet |

---

## 🧪 Examples

**Download a specific range of model IDs:**

python dumptool.py --start 1 --end 100000 --threads 3 -o data --color

**Retry only models that failed previously:**

python dumptool.py --retry -o data --threads 2 --no-color

**Automatically crawl for new models (will stop after many 404s in a row):**

python dumptool.py --crawl -o data --threads 2

**Recheck previously crawled but failed IDs:**

python dumptool.py --recheck-crawled -o data --threads 2

---

## 📌 Notes

- Rate limits may apply — lower threads to 1–2 if you get 429s.
- If `downloaded.txt` is missing or empty, it will be rebuilt by scanning existing JSON files.
- Color output is optional and can be disabled for logging or automation.

---

## 💡 Credits

Built by an archival enthusiast. Use responsibly.  
Not affiliated with Civitai. Data is public and accessible via their API.
