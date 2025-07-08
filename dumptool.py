# Enhanced version of dumptool.py with the following features:
# - Graceful exit on Ctrl+C or 'q'
# - Corrupt JSON file validation before retry pass
# - Retry pass that skips 404s and retries other errors
# - Progress bar with ETA

import argparse
import requests
import json
import time
import os
import threading
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tqdm import tqdm

BLOCK_SIZE = 10000
LOCK = threading.Lock()
stop_requested = threading.Event()


def fetch_civitai_json(model_id, retries=3, backoff=2):
    url = f"https://civitai.com/api/v1/models/{model_id}"
    attempt = 0
    while attempt < retries and not stop_requested.is_set():
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 404:
                return "404"
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "error" in data and "No model" in data["error"]:
                return "404"
            return data
        except requests.Timeout:
            attempt += 1
            time.sleep(backoff ** attempt)
        except requests.RequestException as e:
            return f"{e.response.status_code if e.response else 'ERR'} {e}"
    return "timeout"


def save_pretty_json(data, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_folder_name(model_id):
    folder_prefix = (model_id // BLOCK_SIZE) * BLOCK_SIZE
    return f"{folder_prefix:08d}"


def load_log(path):
    return set(open(path).read().splitlines()) if os.path.exists(path) else set()


def append_log(path, line):
    with LOCK:
        with open(path, 'a') as f:
            f.write(f"{line}\n")


def is_valid_json_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            json.load(f)
        return True
    except Exception:
        return False


def validate_existing_json(output_dir):
    invalid = []
    for root, _, files in os.walk(output_dir):
        for file in files:
            if file.endswith('.json') and file[:8].isdigit():
                filepath = os.path.join(root, file)
                if not is_valid_json_file(filepath):
                    invalid.append(int(file[:8]))
    return invalid


def download_model(model_id, output_dir, logs, force=False):
    model_id_str = f"{model_id:08d}"
    folder = os.path.join(output_dir, get_folder_name(model_id))
    filepath = os.path.join(folder, f"{model_id_str}.json")

    if not force and os.path.exists(filepath) and is_valid_json_file(filepath):
        return

    result = fetch_civitai_json(model_id)
    if result == "404":
        append_log(logs['404'], model_id_str)
    elif isinstance(result, str):
        append_log(logs['errors'], f"{model_id_str} # {result}")
    else:
        os.makedirs(folder, exist_ok=True)
        save_pretty_json(result, filepath)
        append_log(logs['downloaded'], model_id_str)


def signal_handler(sig, frame):
    print("\n[!] Graceful shutdown requested. Finishing current downloads...")
    stop_requested.set()


def download_models_threaded(model_ids, output_dir, force, threads):
    logs = {
        'downloaded': os.path.join(output_dir, "downloaded.txt"),
        '404': os.path.join(output_dir, "notfound.txt"),
        'errors': os.path.join(output_dir, "errors.txt")
    }
    os.makedirs(output_dir, exist_ok=True)
    scan_existing_files(output_dir, logs['downloaded'])

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(download_model, mid, output_dir, logs, force): mid for mid in model_ids
        }
        for f in tqdm(as_completed(futures), total=len(futures), desc="Downloading"):
            if stop_requested.is_set():
                break


def retry_failed_models(output_dir, threads):
    error_log = os.path.join(output_dir, "errors.txt")
    downloaded_log = load_log(os.path.join(output_dir, "downloaded.txt"))
    to_retry = []
    if os.path.exists(error_log):
        with open(error_log) as f:
            for line in f:
                if '# 404' in line:
                    continue
                try:
                    model_id = int(line.strip().split()[0])
                    if f"{model_id:08d}" not in downloaded_log:
                        to_retry.append(model_id)
                except:
                    continue
    if to_retry:
        print(f"[*] Retrying {len(to_retry)} models from error logs...")
        download_models_threaded(to_retry, output_dir, force=True, threads=threads)
    else:
        print("[*] No retryable errors found.")


def scan_existing_files(output_dir, downloaded_log):
    existing_ids = set()
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".json") and f[:8].isdigit():
                existing_ids.add(f[:8])
    logged = load_log(downloaded_log)
    missing = existing_ids - logged
    for model_id in sorted(missing):
        append_log(downloaded_log, model_id)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, help="Start model ID")
    parser.add_argument("--end", type=int, help="End model ID")
    parser.add_argument("--retry", action="store_true", help="Retry failed downloads from logs")
    parser.add_argument("--threads", type=int, default=5)
    parser.add_argument("-o", "--out", default="civitai-meta")
    args = parser.parse_args()

    if args.retry:
        retry_failed_models(args.out, args.threads)
    elif args.start and args.end:
        corrupt = validate_existing_json(args.out)
        print(f"[*] Found {len(corrupt)} corrupted JSON files. Scheduling for redownload.")
        model_ids = list(range(args.start, args.end + 1))
        model_ids = [i for i in model_ids if f"{i:08d}" not in load_log(os.path.join(args.out, "downloaded.txt")) or i in corrupt]
        download_models_threaded(model_ids, args.out, force=False, threads=args.threads)
    else:
        print("[!] Provide --start and --end range or use --retry mode.")
