from collections import deque
import re
import time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

from storage import (
    PDF_DIR,
    compute_sha256,
    get_document_by_sha256,
    get_document_by_source_url,
    init_db,
    record_document,
    sync_sources_from_config,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ActasScraperAcademico/1.0)"
}


def sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return clean[:80] if clean else "acta.pdf"


def is_allowed_html_url(url: str, allowed_domains: set[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc not in allowed_domains:
        return False
    lower_path = parsed.path.lower()
    if lower_path.endswith(".pdf"):
        return False
    if any(ext in lower_path for ext in (".jpg", ".jpeg", ".png", ".gif", ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx")):
        return False
    return True


def is_pdf_response(url: str, response: requests.Response) -> bool:
    content_type = response.headers.get("Content-Type", "").lower()
    return url.lower().endswith(".pdf") or "application/pdf" in content_type


def download_pdf(pdf_url: str, page_url: str, link_text: str, timeout: int = 15) -> str:
    existing = get_document_by_source_url(pdf_url)
    if existing and existing["local_path"]:
        local_file = PDF_DIR / existing["local_path"]
        if local_file.exists() and local_file.stat().st_size > 0:
            return "skipped_existing"

    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        content = resp.content
        if not content:
            record_document(pdf_url, page_url, link_text, None, None, status="error", error_message="Archivo vacío")
            return "error"

        sha = compute_sha256(content)
        dup_by_hash = get_document_by_sha256(sha)
        if dup_by_hash and dup_by_hash["local_path"]:
            dup_file = PDF_DIR / dup_by_hash["local_path"]
            if dup_file.exists():
                record_document(
                    pdf_url,
                    page_url,
                    link_text or dup_by_hash["link_text"],
                    dup_by_hash["local_path"],
                    sha,
                    status=dup_by_hash["status"],
                )
                return "skipped_duplicate_hash"

        parsed_name = urlparse(pdf_url).path.split("/")[-1] or "acta.pdf"
        if not parsed_name.lower().endswith(".pdf"):
            parsed_name += ".pdf"
        safe_name = sanitize_filename(parsed_name)
        stored_name = f"{sha[:10]}_{safe_name}"
        target_path = PDF_DIR / stored_name

        with target_path.open("wb") as f:
            f.write(content)

        display_title = " ".join((link_text or "").split()) or safe_name
        record_document(
            source_url=pdf_url,
            page_url=page_url,
            link_text=display_title,
            local_path=stored_name,
            sha256=sha,
            status="downloaded",
        )
        return "downloaded"

    except Exception as exc:
        record_document(
            source_url=pdf_url,
            page_url=page_url,
            link_text=link_text,
            local_path=None,
            sha256=None,
            status="error",
            error_message=str(exc)[:300],
        )
        return "error"


def scrape_sources(max_depth: int = 3, max_pages: int = 40, delay_seconds: float = 1.0) -> dict:
    init_db()
    seed_urls = sync_sources_from_config()
    allowed_domains = {urlparse(u).netloc for u in seed_urls if urlparse(u).netloc}

    queue = deque((u, 0) for u in seed_urls)
    visited_pages: set[str] = set()
    seen_pdf_urls: set[str] = set()

    stats = {
        "pages_visited": 0,
        "pdfs_downloaded": 0,
        "pdfs_skipped": 0,
        "errors": 0,
    }

    while queue and stats["pages_visited"] < max_pages:
        current_url, depth = queue.popleft()
        current_url = current_url.split("#")[0]

        if current_url in visited_pages or depth > max_depth:
            continue
        visited_pages.add(current_url)

        try:
            resp = requests.get(current_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            stats["pages_visited"] += 1
        except Exception as exc:
            print(f"[AVISO] No se pudo visitar {current_url}: {exc}")
            stats["errors"] += 1
            time.sleep(delay_seconds)
            continue

        if is_pdf_response(current_url, resp):
            if current_url not in seen_pdf_urls:
                seen_pdf_urls.add(current_url)
                res = download_pdf(current_url, current_url, current_url.split("/")[-1])
                if res == "downloaded":
                    stats["pdfs_downloaded"] += 1
                elif res == "error":
                    stats["errors"] += 1
                else:
                    stats["pdfs_skipped"] += 1
            time.sleep(delay_seconds)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue

            abs_url = urljoin(current_url, href).split("#")[0]
            link_text = " ".join(tag.get_text(" ", strip=True).split())

            if abs_url.lower().endswith(".pdf"):
                if abs_url not in seen_pdf_urls:
                    seen_pdf_urls.add(abs_url)
                    res = download_pdf(abs_url, current_url, link_text)
                    if res == "downloaded":
                        stats["pdfs_downloaded"] += 1
                        print(f"[DESCARGADO] {link_text or abs_url}")
                    elif res == "error":
                        stats["errors"] += 1
                        print(f"[ERROR PDF] {abs_url}")
                    else:
                        stats["pdfs_skipped"] += 1
            elif depth < max_depth and is_allowed_html_url(abs_url, allowed_domains):
                if abs_url not in visited_pages:
                    queue.append((abs_url, depth + 1))

        time.sleep(delay_seconds)

    return stats


if __name__ == "__main__":
    resultado = scrape_sources()
    print("\nResumen de recopilación:", resultado)