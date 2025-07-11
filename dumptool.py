#!/usr/bin/env python3
import argparse
import requests
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

BLOCK_SIZE = 10000
LOCK = threading.Lock()

STATUS_MESSAGES = {
    "new": "[NEW] {id} - [{user}] {name} [{status}] ({time})",
    "skip": "[SKIP] {id} - [{user}] {name} [{status}] ({time})",
    "404": "[404] {id} - Not found",
    "429": "[429] {id} - Rate limited",
    "err": "[ERR] {id} - {error}",
}

COLOR_CODES = {
    "new": "32",
    "skip": "36",
    "404": "33",
    "429": "35",
    "err": "31",
    "time": "90",
}

def colorize(text, code, use_color):
    if use_color:
        return f"\033[{code}m{text}\033[0m"
    return text

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

def scan_existing_json(output_dir):
    valid_ids = set()
    count = 0
    for root, _, files in os.walk(output_dir):
        for file in files:
            if file.endswith('.json') and file[:8].isdigit():
                filepath = os.path.join(root, file)
                if is_valid_json_file(filepath):
                    valid_ids.add(file[:8])
                count += 1
                if count % 1000 == 0:
                    print(f"Scanned {count} files so far...", end='\r', flush=True)
    print(f"\n[*] Finished scanning {count} files.")
    return valid_ids

def rebuild_downloaded_log(output_dir, downloaded_log_path):
    valid_ids = scan_existing_json(output_dir)
    with LOCK:
        with open(downloaded_log_path, 'w') as f:
            for vid in sorted(valid_ids):
                f.write(f"{vid}\n")
    return valid_ids

def fetch_civitai_json(model_id, retries=3, backoff=2):
    url = f"https://civitai.com/api/v1/models/{model_id}"
    attempt = 0
    while attempt < retries:
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 404:
                return "404"
            elif response.status_code == 429:
                return "429"
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "error" in data and "No model" in data["error"]:
                return "404"
            return data
        except requests.Timeout:
            attempt += 1
            time.sleep(backoff ** attempt)
        except requests.RequestException as e:
            code = e.response.status_code if e.response else 'ERR'
            return f"{code} {e}"
    return "timeout"

def format_pretty_time(iso_timestamp):
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %I:%M:%S%p")
    except Exception:
        return "timestamp unknown"

def print_model_info(model_id, data, status_type, use_color):
    model_id_str = f"{model_id:08d}"
    try:
        version = data["modelVersions"][0]
        published = version.get("publishedAt") or version.get("createdAt") or "timestamp unknown"
        pretty_time = format_pretty_time(published)
        user = data.get("creator", {}).get("username", "Unknown")
        name = data.get("name", "Unnamed")
        status = version.get("status", "Unknown")

        time_str = colorize(f"({pretty_time})", COLOR_CODES["time"], use_color)
        tag = colorize(STATUS_MESSAGES[status_type].split()[0], COLOR_CODES[status_type], use_color)

        base_msg = STATUS_MESSAGES[status_type].format(
            id=model_id_str, user=user, name=name, status=status, time=pretty_time
        )
        msg = base_msg.rsplit('(',1)[0] + time_str

        print(f"{tag} {msg[len(tag)+1:]}")
    except Exception as e:
        err_tag = colorize(STATUS_MESSAGES["err"].split()[0], COLOR_CODES["err"], use_color)
        print(f"{err_tag} {model_id_str} - Exception printing info: {e}")

def download_model(model_id, output_dir, logs, downloaded_set, use_color, force=False):
    model_id_str = f"{model_id:08d}"
    folder = os.path.join(output_dir, get_folder_name(model_id))
    filepath = os.path.join(folder, f"{model_id_str}.json")

    if not force and os.path.exists(filepath) and is_valid_json_file(filepath) and model_id_str in downloaded_set:
        print_model_info(model_id, json.load(open(filepath, 'r', encoding='utf-8')), "skip", use_color)
        return

    result = fetch_civitai_json(model_id)
    if result == "404":
        print(colorize(STATUS_MESSAGES["404"].format(id=model_id_str), COLOR_CODES["404"], use_color))
        append_log(logs['404'], model_id_str)
    elif result == "429":
        print(colorize(STATUS_MESSAGES["429"].format(id=model_id_str), COLOR_CODES["429"], use_color))
        append_log(logs['errors'], f"{model_id_str} # 429 Too Many Requests")
    elif isinstance(result, str):
        print(colorize(STATUS_MESSAGES["err"].format(id=model_id_str, error=result), COLOR_CODES["err"], use_color))
        append_log(logs['errors'], f"{model_id_str} # {result}")
    else:
        os.makedirs(folder, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=4, ensure_ascii=False)
        append_log(logs['downloaded'], model_id_str)
        print_model_info(model_id, result, "new", use_color)
        downloaded_set.add(model_id_str)

def download_models_threaded(model_ids, output_dir, force, threads, use_color):
    logs = {
        'downloaded': os.path.join(output_dir, "downloaded.txt"),
        '404': os.path.join(output_dir, "notfound.txt"),
        'errors': os.path.join(output_dir, "errors.txt")
    }
    os.makedirs(output_dir, exist_ok=True)
    downloaded_set = load_log(logs['downloaded'])

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(download_model, mid, output_dir, logs, downloaded_set, use_color, force) for mid in model_ids]
        for _ in futures:
            pass

def retry_failed_models(output_dir, threads, use_color):
    error_log = os.path.join(output_dir, "errors.txt")
    downloaded_log = os.path.join(output_dir, "downloaded.txt")
    downloaded_set = load_log(downloaded_log)
    to_retry = []
    if os.path.exists(error_log):
        with open(error_log) as f:
            for line in f:
                if '# 404' in line:
                    continue
                try:
                    model_id = int(line.strip().split()[0])
                    if f"{model_id:08d}" not in downloaded_set:
                        to_retry.append(model_id)
                except:
                    continue
    if to_retry:
        print(f"[*] Retrying {len(to_retry)} models from error logs...")
        download_models_threaded(to_retry, output_dir, force=True, threads=threads, use_color=use_color)
    else:
        print("[*] No retryable errors found.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--threads", type=int, default=5)
    parser.add_argument("-o", "--out", default="civitai-meta")
    parser.add_argument("--color", action="store_true", default=True)
    parser.add_argument("--no-color", action="store_false", dest="color")
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--recheck-crawled", action="store_true")
    args = parser.parse_args()

    downloaded_log_path = os.path.join(args.out, "downloaded.txt")
    if not os.path.exists(downloaded_log_path) or os.path.getsize(downloaded_log_path) == 0:
        print("[*] Rebuilding downloaded.txt from existing JSON files...")
        rebuild_downloaded_log(args.out, downloaded_log_path)

    if args.retry:
        retry_failed_models(args.out, args.threads, args.color)
    elif args.start and args.end:
        downloaded_set = load_log(downloaded_log_path)
        model_ids = list(range(args.start, args.end + 1))
        model_ids = [i for i in model_ids if f"{i:08d}" not in downloaded_set]
        download_models_threaded(model_ids, args.out, force=False, threads=args.threads, use_color=args.color)
    elif args.recheck_crawled:
        crawl_log = os.path.join(args.out, "crawled.txt")
        if os.path.exists(crawl_log):
            crawled_ids = [int(line.strip()) for line in open(crawl_log)]
            download_models_threaded(crawled_ids, args.out, force=True, threads=args.threads, use_color=args.color)
        else:
            print("[!] No crawled.txt found to recheck.")
    elif args.crawl:
        downloaded_set = load_log(downloaded_log_path)
        highest = max(int(i) for i in downloaded_set) if downloaded_set else 1_000_000
        consecutive_404s = 0
        limit = 200
        model_ids = []
        while consecutive_404s < limit:
            highest += 1
            status = fetch_civitai_json(highest)
            if status == "404":
                consecutive_404s += 1
                print(colorize(f"[404] {highest:08d} - Not found", COLOR_CODES["404"], args.color))
            elif isinstance(status, dict):
                model_ids.append(highest)
                append_log(os.path.join(args.out, "crawled.txt"), f"{highest}")
                print_model_info(highest, status, "new", args.color)
                consecutive_404s = 0
            else:
                print(colorize(f"[ERR] {highest:08d} - {status}", COLOR_CODES["err"], args.color))
        print("[*] Crawl complete. Now downloading discovered IDs...")
        download_models_threaded(model_ids, args.out, force=False, threads=args.threads, use_color=args.color)
    else:
        print("[!] Provide --start and --end range, or use --retry, --crawl, or --recheck-crawled mode.")

if __name__ == "__main__":
    main()
