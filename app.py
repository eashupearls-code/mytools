import os
import re
import time
import zipfile
import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
import requests
import streamlit as st
import streamlit.components.v1 as components

# Locate FFmpeg
try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = "ffmpeg"

# =====================================================================
# CONFIGURATION & SECRETS
# =====================================================================
def get_secret(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return os.environ.get(key, default).strip()


PEXELS_API_KEY = get_secret("PEXELS_API_KEY", "")
PIXABAY_API_KEY = get_secret("PIXABAY_API_KEY", "")
UNSPLASH_ACCESS_KEY = get_secret("UNSPLASH_ACCESS_KEY", "")

OUTPUT_DIR = "downloaded_broll"
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_WIKIMEDIA_SIZE_MB = 10.0
UNLIMITED_MEDIA_SIZE_MB = 350.0

GLOBAL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
GRAMMAR_FILLERS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "between", "from", "by"}
ARCHIVAL_MODIFIERS = {
    "cinematic", "drone", "4k", "hd", "1080p", "720p", "slow motion", "timelapse",
    "macro", "establishing shot", "b-roll", "footage of", "clip of", "photo of",
    "photograph of", "picture of", "video of", "film of", "shot of", "footage",
    "clip", "video", "hyperrealistic", "close up", "aerial", "vintage"
}

# =====================================================================
# REPOSITORIES & TOOLS (DOWNLOADERS EXCLUDED)
# =====================================================================
TOOLS = {
    "Stock Video Footage (Pexels)": {
        "tag": "pexels_video",
        "ext": "mp4",
        "desc": "Famous for: High-bitrate cinematic modern b-roll, drone landscapes, highways, and commercial transitions.",
        "type": "video",
        "auth_key": "PEXELS_API_KEY",
        "archival": False
    },
    "Stock Photos (Pexels)": {
        "tag": "pexels_photo",
        "ext": "jpg",
        "desc": "Famous for: Modern commercial photography, clean studio portraits, architecture, and technology stills.",
        "type": "photo",
        "auth_key": "PEXELS_API_KEY",
        "archival": False
    },
    "Pixabay Video Footage": {
        "tag": "pixabay_video",
        "ext": "mp4",
        "desc": "Famous for: Diverse royalty-free nature scenes, slow motion wildlife, animated motion backgrounds, and time-lapses.",
        "type": "video",
        "auth_key": "PIXABAY_API_KEY",
        "archival": False
    },
    "Pixabay Stock Photos": {
        "tag": "pixabay_photo",
        "ext": "jpg",
        "desc": "Famous for: High-resolution stock illustrations, environmental backgrounds, and commercial editorial stills.",
        "type": "photo",
        "auth_key": "PIXABAY_API_KEY",
        "archival": False
    },
    "Unsplash Editorial Photos": {
        "tag": "unsplash_photo",
        "ext": "jpg",
        "desc": "Famous for: Award-winning artistic lighting, dramatic character portraits, street photography, and editorial framing.",
        "type": "photo",
        "auth_key": "UNSPLASH_ACCESS_KEY",
        "archival": False
    },
    "Wikimedia Commons Stills": {
        "tag": "wikimedia_still",
        "ext": "jpg",
        "desc": "Famous for: World public domain repository: antique cartography maps, scientific diagrams, manuscripts, and artwork (≤ 10MB).",
        "type": "photo",
        "auth_key": None,
        "archival": True
    },
    "Library of Congress (Historic Film & Video)": {
        "tag": "loc_video",
        "ext": "mp4",
        "desc": "Famous for: Authentic early 20th-century motion pictures (1890s–1950s), Edison paper prints, Wright Brothers flights, and WWI/WWII silent footage.",
        "type": "video",
        "auth_key": None,
        "archival": True
    },
    "Library of Congress (Historic Photos)": {
        "tag": "loc_photo",
        "ext": "jpg",
        "desc": "Famous for: Civil War glass negatives (Mathew Brady), Great Depression (Dorothea Lange / FSA), historic architecture (HABS), and vintage maps.",
        "type": "photo",
        "auth_key": None,
        "archival": True
    },
    "iStock Photo Explorer (Search & Copy URL)": {
        "tag": "istock_search",
        "ext": "jpg",
        "desc": "Search live iStock catalog by prompt, preview top 3 matches in a single row, and copy official URLs with 1-click.",
        "type": "catalog_explorer",
        "source": "istock",
        "auth_key": None,
        "archival": False
    },
    "Shutterstock Photo Explorer (Search & Copy URL)": {
        "tag": "shutterstock_search",
        "ext": "jpg",
        "desc": "Search live Shutterstock catalog by prompt, preview top 3 matches in a single row, and copy official URLs with 1-click.",
        "type": "catalog_explorer",
        "source": "shutterstock",
        "auth_key": None,
        "archival": False
    }
}

# =====================================================================
# LIVE CATALOG SEARCH: ISTOCK & SHUTTERSTOCK (3-IMAGE ROW)
# =====================================================================
def download_image_buffer(url: str, referer: str = "https://www.google.com/") -> bytes | None:
    headers = {
        "User-Agent": GLOBAL_USER_AGENT,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200 and len(r.content) > 1500:
            return r.content
    except Exception:
        pass
    return None


def search_istock_top3(query: str) -> list[dict]:
    clean_q = requests.utils.quote(query.strip())
    url = f"https://www.istockphoto.com/search/2/image?phrase={clean_q}&sort=mostpopular"
    headers = {
        "User-Agent": GLOBAL_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }
    results = []
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            matches = re.findall(
                r'href="(/photo/[^"]+)"[^>]*>.*?<img[^>]+src="([^">]+)"[^>]*alt="([^"]*)"',
                r.text,
                re.DOTALL
            )
            for page_path, thumb_url, alt_text in matches:
                full_page_url = f"https://www.istockphoto.com{page_path}" if not page_path.startswith("http") else page_path
                img_data = download_image_buffer(thumb_url, referer="https://www.istockphoto.com/")
                if img_data:
                    results.append({
                        "title": alt_text.strip() or "iStock Photo",
                        "image_bytes": img_data,
                        "target_url": full_page_url
                    })
                if len(results) == 3:
                    break
    except Exception:
        pass

    # Unsplash fallback if iStock blocks server IP
    if not results:
        headers_uns = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"} if UNSPLASH_ACCESS_KEY else {"User-Agent": GLOBAL_USER_AGENT}
        try:
            ru = requests.get(f"https://api.unsplash.com/search/photos?query={clean_q}&per_page=3", headers=headers_uns, timeout=8)
            if ru.status_code == 200:
                for photo in ru.json().get("results", [])[:3]:
                    t_url = photo["urls"].get("small") or photo["urls"].get("regular")
                    b = download_image_buffer(t_url)
                    if b:
                        results.append({
                            "title": photo.get("alt_description") or "Commercial Stock Match",
                            "image_bytes": b,
                            "target_url": f"https://www.istockphoto.com/search/2/image?phrase={clean_q}"
                        })
        except Exception:
            pass

    return results


def search_shutterstock_top3(query: str) -> list[dict]:
    clean_q = requests.utils.quote(query.strip())
    results = []

    # Method 1: Shutterstock Next.js Search Hydration Endpoint
    api_url = f"https://www.shutterstock.com/_next/data/en/search/{clean_q}.json?term={clean_q}"
    headers_api = {
        "User-Agent": GLOBAL_USER_AGENT,
        "Accept": "application/json",
        "Referer": f"https://www.shutterstock.com/search/{clean_q}"
    }
    try:
        r = requests.get(api_url, headers=headers_api, timeout=10)
        if r.status_code == 200:
            data = r.json()
            assets = data.get("pageProps", {}).get("initialState", {}).get("search", {}).get("results", {}).get("data", [])
            for item in assets:
                img_id = item.get("id")
                desc = item.get("description", "Shutterstock Photo")
                displays = item.get("displays", {})
                thumb_url = (
                    displays.get("260nw", {}).get("src")
                    or displays.get("preview", {}).get("src")
                    or displays.get("1500w", {}).get("src")
                )
                if img_id and thumb_url:
                    full_page_url = f"https://www.shutterstock.com/image-photo/{img_id}"
                    img_data = download_image_buffer(thumb_url, referer="https://www.shutterstock.com/")
                    if img_data:
                        results.append({
                            "title": desc,
                            "image_bytes": img_data,
                            "target_url": full_page_url
                        })
                if len(results) == 3:
                    break
    except Exception:
        pass

    # Method 2: HTML Search Parsing
    if len(results) < 3:
        try:
            h_url = f"https://www.shutterstock.com/search/{clean_q}"
            h_headers = {
                "User-Agent": GLOBAL_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Referer": "https://www.google.com/"
            }
            hr = requests.get(h_url, headers=h_headers, timeout=10)
            if hr.status_code == 200:
                matches = re.findall(
                    r'<a[^>]+href="(/image-[^"]+)"[^>]*>.*?<img[^>]+src="([^">]+)"[^>]*alt="([^"]*)"',
                    hr.text,
                    re.DOTALL
                )
                for path, thumb, alt in matches:
                    full_url = f"https://www.shutterstock.com{path}" if not path.startswith("http") else path
                    img_data = download_image_buffer(thumb, referer="https://www.shutterstock.com/")
                    if img_data:
                        results.append({
                            "title": alt.strip() or "Shutterstock Photo",
                            "image_bytes": img_data,
                            "target_url": full_url
                        })
                    if len(results) == 3:
                        break
        except Exception:
            pass

    # Method 3: Clean Fallback
    if len(results) < 3:
        try:
            api_url = f"https://pixabay.com/api/?key={PIXABAY_API_KEY}&q={clean_q}&image_type=photo&per_page=3"
            r_pix = requests.get(api_url, timeout=8)
            if r_pix.status_code == 200:
                for hit in r_pix.json().get("hits", [])[:3]:
                    t_url = hit.get("webformatURL")
                    b = download_image_buffer(t_url)
                    if b:
                        results.append({
                            "title": hit.get("tags") or "Shutterstock Catalog Alternative",
                            "image_bytes": b,
                            "target_url": f"https://www.shutterstock.com/search/{clean_q}"
                        })
                    if len(results) == 3:
                        break
        except Exception:
            pass

    return results


# =====================================================================
# QUERY ENGINE & SANITIZATION
# =====================================================================
def prompt_to_clean_filename(prompt: str, ext: str, max_chars: int = 50) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", prompt)
    clean = re.sub(r"[^\w\s-]", "", clean).strip()
    clean = re.sub(r"[\s-]+", "_", clean).lower()
    base_name = clean[:max_chars].strip("_") or "scene_asset"

    candidate = f"{base_name}.{ext}"
    full_path = os.path.join(OUTPUT_DIR, candidate)

    counter = 1
    while os.path.exists(full_path):
        candidate = f"{base_name}_{counter}.{ext}"
        full_path = os.path.join(OUTPUT_DIR, candidate)
        counter += 1

    return candidate


def get_search_queries(raw_prompt: str, is_archival: bool = False) -> list[str]:
    clean = re.sub(r"[^\w\s-]", " ", raw_prompt).strip()
    clean = re.sub(r"\s+", " ", clean)
    queries = [clean]

    words = [w for w in clean.split() if w.lower() not in GRAMMAR_FILLERS]
    keyword_q = " ".join(words)
    if keyword_q and keyword_q != clean:
        queries.append(keyword_q)

    archival_words = [w for w in words if w.lower() not in ARCHIVAL_MODIFIERS]
    archival_q = " ".join(archival_words)
    if archival_q and archival_q not in queries:
        queries.append(archival_q)

    if is_archival and archival_q:
        queries.remove(archival_q)
        queries.insert(0, archival_q)

    return queries


def download_stream(url: str, output_path: str, max_size_mb: float = UNLIMITED_MEDIA_SIZE_MB) -> tuple[bool, str]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    try:
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_bytes = int(r.headers.get("content-length", 0))
            total_mb = total_bytes / (1024 * 1024) if total_bytes else 0

            if max_size_mb and total_mb > max_size_mb:
                return False, f"Exceeded size limit ({total_mb:.1f} MB > {max_size_mb:.0f} MB)"

            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=131072):
                    if chunk:
                        f.write(chunk)

            final_mb = os.path.getsize(output_path) / (1024 * 1024)
            return True, f"{final_mb:.1f} MB"
    except Exception as e:
        if os.path.exists(output_path):
            os.remove(output_path)
        return False, str(e)


def trim_video_stream(cdn_url: str, output_path: str, duration_sec: int) -> tuple[bool, str]:
    cmd_copy = [
        FFMPEG_EXE, "-y",
        "-ss", "00:00:00",
        "-i", cdn_url,
        "-t", str(duration_sec),
        "-c", "copy",
        "-movflags", "+faststart",
        output_path
    ]
    try:
        subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=18)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            sz_mb = os.path.getsize(output_path) / (1024 * 1024)
            return True, f"{sz_mb:.1f} MB ({duration_sec}s clip)"
    except Exception:
        pass

    return download_stream(cdn_url, output_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)


# =====================================================================
# BATCH REPOSITORIES
# =====================================================================
def fetch_pexels_video(query: str, out_path: str, quality_choice: str = "", clip_seconds: int | None = None) -> tuple[bool, str, str | None]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets", None
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "per_page": 6}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        videos = r.json().get("videos", [])
        if not videos:
            return False, "No matching clips found", None

        files = [f for f in videos[0].get("video_files", []) if f.get("link")]
        files.sort(key=lambda x: (x.get("height") or 0), reverse=True)

        chosen = None
        if quality_choice == "4K UHD (2160p)":
            chosen = next((f for f in files if (f.get("height") or 0) >= 2160 or (f.get("width") or 0) >= 3840), None)
        elif quality_choice == "720p HD":
            chosen = next((f for f in files if (f.get("height") or 0) == 720 or (f.get("width") or 0) == 1280), None)

        if not chosen:
            chosen = next((f for f in files if (f.get("height") or 0) == 1080 or (f.get("width") or 0) == 1920), None)
        if not chosen:
            chosen = files[0]

        cdn_url = chosen["link"]
        if clip_seconds:
            ok, msg = trim_video_stream(cdn_url, out_path, clip_seconds)
        else:
            ok, msg = download_stream(cdn_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, cdn_url
    except Exception as e:
        return False, str(e), None


def fetch_pixabay_video(query: str, out_path: str, quality_choice: str = "", clip_seconds: int | None = None) -> tuple[bool, str, str | None]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets", None
    url = "https://pixabay.com/api/videos/"
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 6}
    try:
        r = requests.get(url, params=params, timeout=12)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No clips found", None

        streams = hits[0].get("videos", {})
        chosen = None

        if quality_choice == "4K UHD (2160p)":
            large = streams.get("large", {})
            if (large.get("height") or 0) >= 1440 or (large.get("width") or 0) >= 2560:
                chosen = large
        elif quality_choice == "720p HD":
            medium = streams.get("medium", {})
            if (medium.get("height") or 0) == 720 or (medium.get("width") or 0) == 1280:
                chosen = medium

        if not chosen or not chosen.get("url"):
            chosen = streams.get("large") or streams.get("medium") or streams.get("small")

        if not chosen or not chosen.get("url"):
            return False, "No downloadable stream found", None

        cdn_url = chosen["url"]
        if clip_seconds:
            ok, msg = trim_video_stream(cdn_url, out_path, clip_seconds)
        else:
            ok, msg = download_stream(cdn_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, cdn_url
    except Exception as e:
        return False, str(e), None


def fetch_pexels_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets", None
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        photos = r.json().get("photos", [])
        if not photos:
            return False, "No photos found", None
        src = photos[0].get("src", {})
        img_url = src.get("original") or src.get("large2x") or src.get("large")
        ok, msg = download_stream(img_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_pixabay_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets", None
    url = "https://pixabay.com/api/"
    params = {"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "orientation": "horizontal", "per_page": 5}
    try:
        r = requests.get(url, params=params, timeout=12)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No photos found", None
        img_url = hits[0].get("largeImageURL") or hits[0].get("imageURL")
        ok, msg = download_stream(img_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_unsplash_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    if not UNSPLASH_ACCESS_KEY:
        return False, "UNSPLASH_ACCESS_KEY missing from secrets", None
    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        results = r.json().get("results", [])
        if not results:
            return False, "No photos found", None
        img_url = results[0]["urls"].get("full") or results[0]["urls"].get("regular")
        ok, msg = download_stream(img_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_wikimedia_stills(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    url = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap -filetype:pdf -filetype:audio",
        "gsrnamespace": "6",
        "gsrlimit": "10",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "iiurlwidth": "2560"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        pages = r.json().get("query", {}).get("pages", {})
        if not pages:
            return False, "No matching archival records found", None

        valid_mimes = {"image/jpeg", "image/png", "image/webp"}
        for _, page in pages.items():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]

            if info.get("mime", "").lower() not in valid_mimes:
                continue

            raw_url = info.get("url")
            thumb_url = info.get("thumburl")
            raw_mb = info.get("size", 0) / (1024 * 1024)

            chosen_url = raw_url
            if raw_mb > MAX_WIKIMEDIA_SIZE_MB:
                if thumb_url:
                    chosen_url = thumb_url
                else:
                    continue

            ok, detail = download_stream(chosen_url, out_path, max_size_mb=MAX_WIKIMEDIA_SIZE_MB)
            if ok:
                return True, f"{detail} (Archival Stills <= 10MB)", chosen_url

        return False, "No archival image found within 10MB limit", None
    except Exception as e:
        return False, str(e), None


def fetch_loc_video(query: str, out_path: str, _q: str = "", clip_seconds: int | None = None) -> tuple[bool, str, str | None]:
    url = "https://www.loc.gov/film-and-videos/"
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    params = {"q": query, "fo": "json", "fa": "online-format:video", "c": 6}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        results = r.json().get("results", [])
        if not results:
            return False, "No LOC film records found", None

        for item in results:
            item_id = item.get("id")
            if not item_id:
                continue
            m_res = requests.get(f"{item_id}?fo=json", headers=headers, timeout=12)
            if m_res.status_code != 200:
                continue
            for res in m_res.json().get("resources", []):
                for grp in res.get("files", []):
                    for f in grp:
                        if f.get("url", "").endswith(".mp4"):
                            cdn_url = f["url"]
                            if clip_seconds:
                                ok, msg = trim_video_stream(cdn_url, out_path, clip_seconds)
                            else:
                                ok, msg = download_stream(cdn_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
                            if ok:
                                return True, msg, cdn_url
        return False, "No downloadable MP4 asset found in LOC records", None
    except Exception as e:
        return False, str(e), None


def fetch_loc_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    url = "https://www.loc.gov/photos/"
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    params = {"q": query, "fo": "json", "fa": "online-format:image", "c": 8}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        results = r.json().get("results", [])
        if not results:
            return False, "No LOC photo records found", None

        for item in results:
            chosen_url = None
            img_urls = item.get("image_url", [])
            if isinstance(img_urls, list) and img_urls:
                chosen_url = img_urls[-1]
            elif isinstance(img_urls, str) and img_urls:
                chosen_url = img_urls

            if not chosen_url and item.get("id"):
                m_res = requests.get(f"{item.get('id')}?fo=json", headers=headers, timeout=10)
                if m_res.status_code == 200:
                    for res in m_res.json().get("resources", []):
                        for grp in res.get("files", []):
                            for f in grp:
                                u = f.get("url", "")
                                if any(u.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
                                    chosen_url = u
                                    break
                            if chosen_url:
                                break
                        if chosen_url:
                            break

            if chosen_url:
                if chosen_url.startswith("//"):
                    chosen_url = "https:" + chosen_url
                ok, detail = download_stream(chosen_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
                if ok:
                    return True, detail, chosen_url
        return False, "No downloadable image stream found in LOC record", None
    except Exception as e:
        return False, str(e), None


# =====================================================================
# THREAD DISPATCH & MEMORY ZIP
# =====================================================================
ENGINE_MAP = {
    "Stock Video Footage (Pexels)": fetch_pexels_video,
    "Stock Photos (Pexels)": fetch_pexels_photo,
    "Pixabay Video Footage": fetch_pixabay_video,
    "Pixabay Stock Photos": fetch_pixabay_photo,
    "Unsplash Editorial Photos": fetch_unsplash_photo,
    "Wikimedia Commons Stills": fetch_wikimedia_stills,
    "Library of Congress (Historic Film & Video)": fetch_loc_video,
    "Library of Congress (Historic Photos)": fetch_loc_photo
}


def process_single_prompt(prompt: str, tool_name: str, ext: str, quality_choice: str, clip_seconds: int | None):
    filename = prompt_to_clean_filename(prompt, ext)
    out_path = os.path.join(OUTPUT_DIR, filename)
    tool_info = TOOLS[tool_name]
    is_archival = tool_info.get("archival", False)
    query_candidates = get_search_queries(prompt, is_archival=is_archival)
    fetch_func = ENGINE_MAP[tool_name]

    t0 = time.time()
    ok = False
    detail = "Search returned no records"
    cdn_url = None

    for q in query_candidates:
        ok, detail, cdn_url = fetch_func(q, out_path, quality_choice, clip_seconds)
        if ok:
            break

    elapsed = time.time() - t0

    return {
        "prompt": prompt,
        "filename": filename,
        "path": out_path,
        "ext": ext,
        "ok": ok,
        "detail": detail,
        "cdn_url": cdn_url,
        "elapsed": elapsed
    }


def create_in_memory_zip(file_list: list[str]) -> bytes:
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for f in file_list:
            if os.path.exists(f):
                zf.write(f, arcname=os.path.basename(f))
    mem_zip.seek(0)
    return mem_zip.read()


# =====================================================================
# STREAMLIT UI SETUP
# =====================================================================
st.set_page_config(page_title="Automation Tools By Shoaib Malik", page_icon="🎬", layout="wide")

st.markdown("""
<style>
    div[data-testid="stRadio"] label p {
        font-size: 1.15rem !important;
        font-weight: 600 !important;
        line-height: 1.8 !important;
    }
    div[data-testid="stRadio"] [data-baseweb="radio"] div:first-child {
        transform: scale(1.35);
        margin-right: 0.6rem !important;
    }
    h3, h4 {
        font-weight: 700 !important;
    }
</style>
""", unsafe_allow_html=True)

# =====================================================================
# SECURITY LOGIN GATEKEEPER
# =====================================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("# 🎬 **Automation Tools By Shoaib Malik**")
    st.caption("High-speed B-roll, public domain & stock portal pipeline for documentary research.")
    st.divider()

    _, col_login, _ = st.columns([1, 1.2, 1])
    with col_login:
        st.markdown("### 🔒 **Security Verification**")
        st.caption("Please log in with your authorized credentials to access this tool.")
        with st.form("login_form"):
            input_username = st.text_input("Username")
            input_password = st.text_input("Password", type="password")
            submit_login = st.form_submit_button("Unlock Studio", type="primary", use_container_width=True)

            if submit_login:
                if input_username == "Malik" and input_password == "Shoaib@10":
                    st.session_state.authenticated = True
                    st.success("Access Granted!")
                    st.rerun()
                else:
                    st.error("Incorrect Username or Password. Access Denied.")

    st.stop()

# Initialize session caches
if "batch_results" not in st.session_state:
    st.session_state.batch_results = []
if "zip_bytes" not in st.session_state:
    st.session_state.zip_bytes = None
if "last_tool_used" not in st.session_state:
    st.session_state.last_tool_used = ""

# Explorer Search State
if "catalog_search_results" not in st.session_state:
    st.session_state.catalog_search_results = []
if "catalog_search_query" not in st.session_state:
    st.session_state.catalog_search_query = ""

# =====================================================================
# AUTHENTICATED WORKSPACE
# =====================================================================
col_header, col_logout = st.columns([4, 1])
with col_header:
    st.markdown("# 🎬 **Automation Tools By Shoaib Malik**")
    st.caption("⚡ Modern Stock + Public Domain Archives + Stock Search & Link Extractors")
with col_logout:
    st.write("")
    if st.button("🔒 **Log Out**", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.batch_results = []
        st.session_state.zip_bytes = None
        st.session_state.catalog_search_results = []
        st.rerun()

st.divider()

col_nav, col_main = st.columns([1, 2.3])

with col_nav:
    st.markdown("### **Select Source Tool**")
    selected_tool_name = st.radio(
        "Available Repositories:",
        list(TOOLS.keys()),
        index=0,
        label_visibility="collapsed"
    )

tool_info = TOOLS[selected_tool_name]

if st.session_state.last_tool_used != selected_tool_name:
    st.session_state.batch_results = []
    st.session_state.zip_bytes = None
    st.session_state.catalog_search_results = []
    st.session_state.last_tool_used = selected_tool_name

with col_main:
    st.markdown(f"### **Tool: {selected_tool_name}**")
    st.info(tool_info["desc"])

    # =================================================================
    # TOOL A: 3-IMAGE ROW CATALOG EXPLORER (HOTLINK-BYPASSED PREVIEWS)
    # =================================================================
    if tool_info["type"] == "catalog_explorer":
        source_brand = tool_info["source"]
        st.markdown(f"#### 🔍 **Live {source_brand.capitalize()} Catalog Search**")
        st.caption(f"Enter any prompt below. The tool will search {source_brand.capitalize()}, display the top 3 matches in a single clean row, and let you copy the URL with 1-click.")

        col_search_bar, col_search_go = st.columns([3, 1])
        with col_search_bar:
            catalog_query = st.text_input(
                f"Enter {source_brand.capitalize()} Search Prompt:",
                placeholder="e.g. stylish interior living room design wooden, executive boardroom, cyberpunk night city",
                label_visibility="collapsed"
            )
        with col_search_go:
            run_catalog_search = st.button("🔍 **Search Images**", type="primary", use_container_width=True)

        if run_catalog_search:
            if not catalog_query.strip():
                st.warning("Please enter a search prompt.")
            else:
                st.session_state.catalog_search_query = catalog_query.strip()
                with st.spinner(f"Querying {source_brand.capitalize()} and loading previews..."):
                    if source_brand == "istock":
                        items = search_istock_top3(catalog_query)
                    else:
                        items = search_shutterstock_top3(catalog_query)

                    st.session_state.catalog_search_results = items

                if not items:
                    st.error(f"No results returned from {source_brand.capitalize()}. Try broader keywords.")
                else:
                    st.success(f"✓ Displaying top 3 results from {source_brand.capitalize()}!")

        # Render exactly 3 photos in a single clean row
        if st.session_state.catalog_search_results:
            st.markdown("---")
            st.markdown(f"#### **Top 3 Results for: *\"{st.session_state.catalog_search_query}\"***")

            items = st.session_state.catalog_search_results[:3]
            cols = st.columns(3)

            for idx, item in enumerate(items):
                with cols[idx]:
                    st.image(item["image_bytes"], use_container_width=True)
                    st.caption(f"**{item['title'][:40]}...**" if len(item['title']) > 40 else f"**{item['title']}**")

                    raw_url = item["target_url"]
                    btn_id = f"cp_btn_{idx}"
                    copy_component_html = f"""
                    <div style="margin-bottom: 15px;">
                        <input type="text" value="{raw_url}" id="url_val_{idx}" readonly style="
                            width: 100%;
                            padding: 6px 8px;
                            font-size: 11px;
                            border: 1px solid #d0d7de;
                            border-radius: 4px;
                            background: #f6f8fa;
                            color: #24292f;
                            margin-bottom: 6px;
                            box-sizing: border-box;
                        ">
                        <button id="{btn_id}" onclick="
                            const inp = document.getElementById('url_val_{idx}');
                            navigator.clipboard.writeText(inp.value);
                            const b = document.getElementById('{btn_id}');
                            b.innerText = '✓ Copied!';
                            b.style.background = '#2ea44f';
                            setTimeout(() => {{
                                b.innerText = '📋 Copy Link';
                                b.style.background = '#0969da';
                            }}, 1800);
                        " style="
                            width: 100%;
                            background-color: #0969da;
                            color: white;
                            border: none;
                            padding: 8px 12px;
                            font-size: 13px;
                            font-weight: 600;
                            border-radius: 6px;
                            cursor: pointer;
                        ">
                            📋 Copy Link
                        </button>
                    </div>
                    """
                    components.html(copy_component_html, height=85)

    # =================================================================
    # TOOL B: STOCK & ARCHIVAL BATCH SOURCING
    # =================================================================
    else:
        auth_key_name = tool_info.get("auth_key")
        if auth_key_name:
            current_key = globals().get(auth_key_name, "")
            if not current_key:
                st.warning(f"⚠️ `{auth_key_name}` is not configured in your Streamlit Secrets vault.")

        quality_choice = "1080p Full HD"
        clip_seconds = 10

        if tool_info["type"] == "video":
            col_q1, col_q2 = st.columns(2)
            with col_q1:
                st.markdown("**Clip Length**")
                clip_label = st.selectbox(
                    "Clip Length",
                    ["10 Seconds (Default)", "15 Seconds", "Full Video Length"],
                    index=0,
                    label_visibility="collapsed"
                )
                if clip_label == "10 Seconds (Default)":
                    clip_seconds = 10
                elif clip_label == "15 Seconds":
                    clip_seconds = 15
                else:
                    clip_seconds = None

            with col_q2:
                st.markdown("**Quality**")
                quality_choice = st.selectbox(
                    "Quality",
                    ["1080p Full HD", "4K UHD (2160p)", "720p HD"],
                    index=0,
                    label_visibility="collapsed"
                )

        st.markdown("**Visual Prompts (one prompt per line)**")
        prompt_input = st.text_area(
            "Visual Prompts",
            height=160,
            placeholder="wright brothers first flight kitty hawk\ncivil war battlefield photography\nmodern corporate boardroom meeting",
            label_visibility="collapsed"
        )

        col_btn1, col_btn2 = st.columns([1.3, 1])
        with col_btn1:
            start_btn = st.button("⚡ **Start Fast Parallel Sourcing**", type="primary", use_container_width=True)
        with col_btn2:
            if st.session_state.batch_results:
                if st.button("**Clear Results**", use_container_width=True):
                    st.session_state.batch_results = []
                    st.session_state.zip_bytes = None
                    st.rerun()

        if start_btn:
            lines = [line.strip() for line in prompt_input.splitlines() if line.strip()]
            if not lines:
                st.warning("Please enter at least one visual prompt.")
            else:
                ext = tool_info["ext"]
                st.session_state.batch_results = []
                st.session_state.zip_bytes = None

                with st.spinner(f"Fetching {len(lines)} asset(s) simultaneously from edge servers..."):
                    t_all = time.time()

                    with ThreadPoolExecutor(max_workers=min(len(lines), 8)) as executor:
                        futures = [
                            executor.submit(process_single_prompt, line, selected_tool_name, ext, quality_choice, clip_seconds)
                            for line in lines
                        ]
                        results = [f.result() for f in futures]

                    st.session_state.batch_results = results

                    valid_paths = [r["path"] for r in results if r["ok"] and os.path.exists(r["path"])]
                    if valid_paths:
                        st.session_state.zip_bytes = create_in_memory_zip(valid_paths)

                st.success(f"✓ Retrieved {len(valid_paths)} of {len(lines)} items in {time.time() - t_all:.1f}s total!")
                st.rerun()

        # Results View
        if st.session_state.batch_results:
            results = st.session_state.batch_results
            successful = [r for r in results if r["ok"]]
            failed = [r for r in results if not r["ok"]]

            if successful:
                st.markdown("### **Download Your Sourced Assets**")

                cdn_links = [{"url": r["cdn_url"], "name": r["filename"]} for r in successful if r.get("cdn_url")]

                if cdn_links:
                    js_code = """
                    <script>
                    async function downloadOneByOne() {
                        const links = """ + str(cdn_links) + """;
                        const btn = document.getElementById('seqBtn');
                        btn.disabled = true;
                        btn.style.opacity = '0.6';

                        for (let i = 0; i < links.length; i++) {
                            const item = links[i];
                            btn.innerText = '⚡ Downloading (' + (i + 1) + '/' + links.length + ')...';
                            try {
                                const res = await fetch(item.url);
                                const blob = await res.blob();
                                const blobUrl = window.URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.style.display = 'none';
                                a.href = blobUrl;
                                a.download = item.name;
                                document.body.appendChild(a);
                                a.click();
                                window.URL.revokeObjectURL(blobUrl);
                                document.body.removeChild(a);
                            } catch (err) {
                                const a = document.createElement('a');
                                a.href = item.url;
                                a.download = item.name;
                                a.target = '_blank';
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                            }
                            if (i < links.length - 1) {
                                await new Promise(r => setTimeout(r, 2000));
                            }
                        }

                        btn.disabled = false;
                        btn.style.opacity = '1';
                        btn.innerText = '✓ All Files Downloaded!';
                    }
                    </script>
                    <div style="padding: 2px 0;">
                        <button id="seqBtn" onclick="downloadOneByOne()" style="
                            background: linear-gradient(135deg, #00C853 0%, #009624 100%);
                            color: white;
                            border: none;
                            padding: 13px 20px;
                            font-size: 15px;
                            font-weight: 700;
                            border-radius: 8px;
                            cursor: pointer;
                            width: 100%;
                            margin-bottom: 6px;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.12);
                        ">
                            ⚡ Download One-by-One (2s Gap - Max Regional Speed)
                        </button>
                    </div>
                    """
                    components.html(js_code, height=65)

                if st.session_state.zip_bytes:
                    zip_mb = len(st.session_state.zip_bytes) / (1024 * 1024)
                    st.download_button(
                        label=f"📦 **Download All as Single Archive (.ZIP) — [{zip_mb:.1f} MB]**",
                        data=st.session_state.zip_bytes,
                        file_name="broll_assets.zip",
                        mime="application/zip",
                        type="primary",
                        use_container_width=True
                    )

            st.divider()
            st.markdown("#### **Sourced File Status & Previews**")

            for r in failed:
                st.error(f"✖ **Failed:** \"{r['prompt']}\" — {r['detail']}")

            for r in successful:
                st.success(f"✓ **Saved:** `{r['filename']}` — {r['detail']} (Fetched in {r['elapsed']:.1f}s)")
                col_prev, col_meta = st.columns([1.6, 1.2])
                with col_prev:
                    if r["ext"] == "mp4" and os.path.exists(r["path"]):
                        st.video(r["path"])
                    elif os.path.exists(r["path"]):
                        st.image(r["path"], use_container_width=True)
                with col_meta:
                    st.markdown(f"**Prompt:** {r['prompt']}")
                    st.markdown(f"**Filename:** `{r['filename']}`")
                    st.caption(f"File Size: {r['detail']}")
                    if r.get("cdn_url"):
                        st.link_button("🌐 Open Source File", r["cdn_url"], use_container_width=True)
                st.write("---")
