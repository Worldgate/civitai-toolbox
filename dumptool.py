#!/usr/bin/env python3

import argparse
import requests
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BLOCK_SIZE = 10000
LOCK = threading.Lock()


def fetch_civitai_json(model_id, retries=3, backoff=2):
    url = f"https://civitai.com/api/v1/models/{model_id}"
    attempt = 0
    while attempt < retries:
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
            print(f"[WARN] Timeout on {model_id}, retry {attempt + 1}/{retries}")
            attempt += 1
            time.sleep(backoff ** attempt)
        except requests.RequestException as e:
            print(f"[ERR] {model_id} - {e}")
            return str(e)
    return "timeout"


def save_pretty_json(data, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_folder_name(model_id):
    folder_prefix = (model_id // BLOCK_SIZE) * BLOCK_SIZE
    return f"{folder_prefix:08d}"


def load_log(path):
    return set(open(path).read().splitlines()) if os.path.exists(path) else set()


def append_log(path, model_id):
    with LOCK:
        with open(path, 'a') as f:
            f.write(f"{model_id}\n")


def scan_existing_files(output_dir, downloaded_log):
    print("[*] Scanning existing files...")
    existing_ids = set()
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".json") and f[:8].isdigit():
                existing_ids.add(f[:8])
    logged = load_log(downloaded_log)
    missing = existing_ids - logged
    if missing:
        print(f"[+] Adding {len(missing)} to downloaded log")
        with open(downloaded_log, 'a') as f:
            for model_id in sorted(missing):
                f.write(model_id + "\n")


def format_model_info(model_id_str, model_json):
    name = model_json.get("name") or "Unknown"
    availability = model_json.get("availability") or "Public"
    created_raw = model_json.get("createdAt")

    creator_obj = model_json.get("creator")
    if isinstance(creator_obj, dict):
        creator = creator_obj.get("username", "Unknown")
    else:
        creator = "Unknown"

    if created_raw:
        try:
            dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            created_str = dt.strftime("%Y-%m-%d @ %H:%M:%S")
        except Exception:
            created_str = "??"
    else:
        created_str = "??"

    return f"[NEW] {model_id_str} - [{creator}] {name} [{availability}] [{created_str}]"


def download_model(model_id, output_dir, downloaded_log, notfound_log, force=False):
    model_id_str = f"{model_id:08d}"
    folder = os.path.join(output_dir, get_folder_name(model_id))
    filepath = os.path.join(folder, f"{model_id_str}.json")

    if not force and os.path.exists(filepath):
        return

    result = fetch_civitai_json(model_id)
    if result == "404":
        print(f"[404] {model_id_str}")
        append_log(notfound_log, model_id_str)
        return
    elif isinstance(result, str):
        print(f"[ERR] {model_id_str} - {result}")
        return

    os.makedirs(folder, exist_ok=True)
    save_pretty_json(result, filepath)
    append_log(downloaded_log, model_id_str)
    print(format_model_info(model_id_str, result))


def parse_model_ids(input_str):
    ids = set()
    for part in input_str.split(','):
        part = part.strip()
        if '-' in part:
            start, end = map(int, part.split('-', 1))
            ids.update(range(start, end + 1))
        else:
            ids.add(int(part))
    return sorted(ids)


def download_models_threaded(model_ids, output_dir, delay, force, threads):
    downloaded_log = os.path.join(output_dir, "downloaded.txt")
    notfound_log = os.path.join(output_dir, "notfound.txt")
    os.makedirs(output_dir, exist_ok=True)
    scan_existing_files(output_dir, downloaded_log)

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [
            executor.submit(download_model, mid, output_dir, downloaded_log, notfound_log, force)
            for mid in model_ids
        ]
        for f in as_completed(futures):
            pass
    print("[*] Done.")


def get_latest_max_model_id():
    url = "https://civitai.com/api/v1/models?limit=100&sort=Newest"
    try:
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        ids = [item["id"] for item in data.get("items", [])]
        return data.get("items", []), max(ids) if ids else None
    except Exception as e:
        print(f"[!] Failed to get latest models: {e}")
        return [], None


def read_last_max(path):
    return int(open(path).read()) if os.path.exists(path) else 0


def write_last_max(path, val):
    with open(path, 'w') as f:
        f.write(str(val))


def update_recent_models(output_dir, count=100):
    downloaded_log = os.path.join(output_dir, "downloaded.txt")
    items, max_id = get_latest_max_model_id()
    if not items:
        return None
    for item in sorted(items, key=lambda x: x["id"]):
        mid = item["id"]
        folder = os.path.join(output_dir, get_folder_name(mid))
        os.makedirs(folder, exist_ok=True)
        filepath = os.path.join(folder, f"{mid:08d}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(item, f, indent=4, ensure_ascii=False)
        print(f"[UPD] {mid:08d}")
    scan_existing_files(output_dir, downloaded_log)
    return max_id


def resume_missing(output_dir, delay, force, threads):
    downloaded = load_log(os.path.join(output_dir, "downloaded.txt"))
    notfound = load_log(os.path.join(output_dir, "notfound.txt"))
    ceiling = read_last_max(os.path.join(output_dir, "last_max.txt"))
    to_download = [
        i for i in range(1, ceiling + 1)
        if f"{i:08d}" not in downloaded and f"{i:08d}" not in notfound
    ]
    print(f"[*] Resuming {len(to_download)} missing files up to ID {ceiling}...")
    download_models_threaded(to_download, output_dir, delay, force, threads)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Civitai model JSON archiver with threads, resume, and update.")
    parser.add_argument("model_ids", nargs="?", help="e.g. 1000-1050,1001 or 'auto'")
    parser.add_argument("--resume", action="store_true", help="Resume all missing files up to last_max.txt")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between requests (if needed)")
    parser.add_argument("--force", action="store_true", help="Force redownload even if file exists")
    parser.add_argument("--threads", type=int, default=5, help="Number of parallel downloads")
    parser.add_argument("-o", "--out", default="civitai-meta", help="Output folder")

    args = parser.parse_args()

    if args.resume:
        resume_missing(args.out, args.delay, args.force, args.threads)
    elif args.model_ids == "auto":
        max_id = update_recent_models(args.out)
        if max_id:
            prev = read_last_max(os.path.join(args.out, "last_max.txt"))
            if max_id > prev:
                write_last_max(os.path.join(args.out, "last_max.txt"), max_id)
                ids = list(range(prev + 1, max_id + 1))
                download_models_threaded(ids, args.out, args.delay, args.force, args.threads)
    elif args.model_ids:
        ids = parse_model_ids(args.model_ids)
        download_models_threaded(ids, args.out, args.delay, args.force, args.threads)
    else:
        print("[!] Please provide model_ids, --resume, or 'auto'")
