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
import yt_dlp

# Locate FFmpeg
try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = "ffmpeg"

# Optional fast transcript library
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

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
FLICKR_API_KEY = get_secret("FLICKR_API_KEY", "")

OUTPUT_DIR = "downloaded_broll"
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_WIKIMEDIA_SIZE_MB = 10.0
UNLIMITED_MEDIA_SIZE_MB = 350.0

GLOBAL_USER_AGENT = "BrollStudioArchive/3.0 (documentary_research_tool; contact@studio.local)"
GRAMMAR_FILLERS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "between", "from", "by"}
ARCHIVAL_MODIFIERS = {
    "cinematic", "drone", "4k", "hd", "1080p", "720p", "slow motion", "timelapse",
    "macro", "establishing shot", "b-roll", "footage of", "clip of", "photo of",
    "photograph of", "picture of", "video of", "film of", "shot of", "footage",
    "clip", "video", "hyperrealistic", "close up", "aerial", "vintage"
}

# =====================================================================
# REPOSITORIES & SPECIALIZATIONS
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
    "National Archives (NARA Historical Footage)": {
        "tag": "nara_video",
        "ext": "mp4",
        "desc": "Famous for: Declassified US military operations (WWII, Korea, Vietnam), NASA Apollo space missions, and historical newsreels.",
        "type": "video",
        "auth_key": None,
        "archival": True
    },
    "National Archives (NARA Historical Stills)": {
        "tag": "nara_photo",
        "ext": "jpg",
        "desc": "Famous for: Official US government records, WWII wartime posters, military photography, Documerica project, and presidential libraries.",
        "type": "photo",
        "auth_key": None,
        "archival": True
    },
    "Internet Archive (Prelinger & Historic Video)": {
        "tag": "ia_video",
        "ext": "mp4",
        "desc": "Famous for: 60,000+ Prelinger Archives educational/industrial films (1927–1987), vintage commercials, public domain movies, and retro B-roll.",
        "type": "video",
        "auth_key": None,
        "archival": True
    },
    "Internet Archive (Vintage Stills & Scans)": {
        "tag": "ia_photo",
        "ext": "jpg",
        "desc": "Famous for: Rare antique book illustrations, retro advertising prints, vintage magazine scans, NASA photography, and historical art.",
        "type": "photo",
        "auth_key": None,
        "archival": True
    },
    "Flickr Commons (Global Cultural Heritage)": {
        "tag": "flickr_commons",
        "ext": "jpg",
        "desc": "Famous for: Public photography contributed by 100+ global institutions (Smithsonian, British Library, State Library of NSW) documenting 19th & 20th-century world culture.",
        "type": "photo",
        "auth_key": None,
        "archival": True
    },
    "YouTube Transcript Precision Trimmer": {
        "tag": "yt_transcript_cutter",
        "ext": "mp4",
        "desc": "Famous for: Searching spoken dialogue/quotes across any YouTube video transcript, pinpointing exact timestamps, and trimming precision clips.",
        "type": "transcript_cutter",
        "auth_key": None,
        "archival": False
    }
}

# =====================================================================
# SEARCH & QUERY ENGINE
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


def format_seconds_to_timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def time_to_seconds(t_str: str) -> float | None:
    parts = str(t_str).strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


def extract_youtube_video_id(url: str) -> str | None:
    regex = r"(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([0-9A-Za-z_-]{11})"
    match = re.search(regex, url.strip())
    return match.group(1) if match else None


def fetch_youtube_transcript_data(video_id: str) -> tuple[bool, list[dict] | str]:
    """Fetches YouTube transcripts via YouTubeTranscriptApi or yt-dlp subtitle scraper."""
    if YouTubeTranscriptApi:
        try:
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id)
                return True, transcript
            except Exception:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                for t in transcript_list:
                    return True, t.fetch()
        except Exception:
            pass

    # yt-dlp subtitle extraction fallback
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en"],
        "quiet": True,
        "no_warnings": True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            subs = info.get("subtitles") or info.get("automatic_captions")
            if subs:
                for lang in subs:
                    for fmt in subs[lang]:
                        if fmt.get("ext") == "json3":
                            r = requests.get(fmt["url"], timeout=10)
                            events = r.json().get("events", [])
                            parsed = []
                            for ev in events:
                                segs = ev.get("segs", [])
                                txt = "".join(s.get("utf8", "") for s in segs).strip()
                                if txt and txt != "\n":
                                    parsed.append({
                                        "start": ev.get("tStartMs", 0) / 1000.0,
                                        "duration": ev.get("dDurationMs", 0) / 1000.0,
                                        "text": txt
                                    })
                            if parsed:
                                return True, parsed
    except Exception as e:
        return False, str(e)

    return False, "Could not retrieve transcript or captions for this YouTube video."


def trim_youtube_stream_slice(url: str, s_sec: float, e_sec: float, out_path: str, quality_choice: str, mute_audio: bool) -> tuple[bool, str]:
    """Directly extracts and transcodes high-res YouTube clip slice with android/web client."""
    clean_url = url.strip().split("?si=")[0].split("&si=")[0]
    duration = e_sec - s_sec

    if quality_choice == "4K UHD (2160p)":
        v_filter = "bestvideo[height<=2160]/bestvideo/best"
    elif quality_choice == "720p HD":
        v_filter = "bestvideo[height<=720]/bestvideo/best"
    else:
        v_filter = "bestvideo[height<=1080]/bestvideo/best"

    ffmpeg_post_args = [
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart"
    ]

    if mute_audio:
        target_format = v_filter
        ffmpeg_post_args.append("-an")
    else:
        target_format = f"{v_filter}+bestaudio/best"
        ffmpeg_post_args.extend(["-c:a", "aac", "-b:a", "192k"])

    ydl_opts = {
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        },
        "format": target_format,
        "outtmpl": out_path,
        "external_downloader": "ffmpeg",
        "external_downloader_args": {
            "ffmpeg_i": ["-ss", str(s_sec), "-to", str(e_sec)],
            "ffmpeg": ffmpeg_post_args
        },
        "ffmpeg_location": FFMPEG_EXE,
        "overwrites": True,
        "quiet": True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([clean_url])

        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            sz_mb = os.path.getsize(out_path) / (1024 * 1024)
            return True, f"{sz_mb:.1f} MB ({duration:.1f}s segment)"

        base_no_ext, _ = os.path.splitext(out_path)
        for ext in [".mkv", ".webm", ".ts"]:
            alt = f"{base_no_ext}{ext}"
            if os.path.exists(alt) and os.path.getsize(alt) > 1000:
                os.rename(alt, out_path)
                sz_mb = os.path.getsize(out_path) / (1024 * 1024)
                return True, f"{sz_mb:.1f} MB ({duration:.1f}s segment)"

        return False, "Failed to capture trimmed clip from YouTube."
    except Exception as e:
        return False, str(e)


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
# REPOSITORY ENGINES
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


def fetch_ia_video(query: str, out_path: str, _q: str = "", clip_seconds: int | None = None) -> tuple[bool, str, str | None]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    search_url = "https://archive.org/advancedsearch.php"
    params = {
        "q": f"({query}) AND mediatype:(movies)",
        "fl[]": "identifier,title",
        "rows": 6,
        "page": 1,
        "output": "json"
    }
    try:
        r = requests.get(search_url, params=params, headers=headers, timeout=15)
        docs = r.json().get("response", {}).get("docs", [])
        if not docs:
            return False, "No Internet Archive video records found", None

        for doc in docs:
            ident = doc.get("identifier")
            if not ident:
                continue
            meta_url = f"https://archive.org/metadata/{ident}/files"
            m_res = requests.get(meta_url, headers=headers, timeout=12)
            if m_res.status_code != 200:
                continue
            files = m_res.json().get("result", [])
            mp4_files = [f for f in files if f.get("name", "").lower().endswith(".mp4")]
            if not mp4_files:
                continue

            mp4_files.sort(key=lambda x: int(x.get("size", 0)), reverse=True)
            chosen_file = mp4_files[0]
            cdn_url = f"https://archive.org/download/{ident}/{chosen_file['name']}"

            if clip_seconds:
                ok, msg = trim_video_stream(cdn_url, out_path, clip_seconds)
            else:
                ok, msg = download_stream(cdn_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
            if ok:
                return True, msg, cdn_url
        return False, "No downloadable MP4 asset found in Internet Archive", None
    except Exception as e:
        return False, str(e), None


def fetch_ia_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    search_url = "https://archive.org/advancedsearch.php"
    params = {
        "q": f"({query}) AND mediatype:(image)",
        "fl[]": "identifier,title",
        "rows": 6,
        "page": 1,
        "output": "json"
    }
    try:
        r = requests.get(search_url, params=params, headers=headers, timeout=15)
        docs = r.json().get("response", {}).get("docs", [])
        if not docs:
            return False, "No Internet Archive image records found", None

        valid_exts = (".jpg", ".jpeg", ".png")
        for doc in docs:
            ident = doc.get("identifier")
            if not ident:
                continue
            meta_url = f"https://archive.org/metadata/{ident}/files"
            m_res = requests.get(meta_url, headers=headers, timeout=12)
            if m_res.status_code != 200:
                continue
            files = m_res.json().get("result", [])
            img_files = [
                f for f in files
                if any(f.get("name", "").lower().endswith(ext) for ext in valid_exts)
                and not f.get("name", "").lower().endswith("_thumb.jpg")
            ]
            if not img_files:
                continue

            img_files.sort(key=lambda x: int(x.get("size", 0)), reverse=True)
            chosen_file = img_files[0]
            cdn_url = f"https://archive.org/download/{ident}/{chosen_file['name']}"

            ok, detail = download_stream(cdn_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
            if ok:
                return True, detail, cdn_url
        return False, "No downloadable image found in Internet Archive", None
    except Exception as e:
        return False, str(e), None


def fetch_nara_video(query: str, out_path: str, _q: str = "", clip_seconds: int | None = None) -> tuple[bool, str, str | None]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    try:
        url = "https://catalog.archives.gov/proxy/v3/records/search"
        params = {"q": query, "typeOfMaterials": "moving images", "limit": 5}
        r = requests.get(url, params=params, headers=headers, timeout=12)
        hits = r.json().get("body", {}).get("hits", {}).get("hits", [])
        for hit in hits:
            record = hit.get("_source", {}).get("record", {}) or hit.get("_source", {})
            objs = record.get("digitalObjects", []) or []
            for obj in objs:
                obj_url = obj.get("objectUrl") or obj.get("accessUrl") or ""
                if obj_url.lower().endswith(".mp4"):
                    if clip_seconds:
                        ok, msg = trim_video_stream(obj_url, out_path, clip_seconds)
                    else:
                        ok, msg = download_stream(obj_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
                    if ok:
                        return True, msg, obj_url
    except Exception:
        pass

    return fetch_ia_video(f"{query} FedFlix", out_path, clip_seconds=clip_seconds)


def fetch_nara_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    try:
        url = "https://catalog.archives.gov/proxy/v3/records/search"
        params = {"q": query, "typeOfMaterials": "photographs and other graphic materials", "limit": 6}
        r = requests.get(url, params=params, headers=headers, timeout=12)
        hits = r.json().get("body", {}).get("hits", {}).get("hits", [])
        for hit in hits:
            record = hit.get("_source", {}).get("record", {}) or hit.get("_source", {})
            objs = record.get("digitalObjects", []) or []
            for obj in objs:
                obj_url = obj.get("objectUrl") or obj.get("accessUrl") or ""
                if any(obj_url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
                    ok, detail = download_stream(obj_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
                    if ok:
                        return True, detail, obj_url
    except Exception:
        pass

    return fetch_wikimedia_stills(f"{query} National Archives and Records Administration", out_path)


def fetch_flickr_commons(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    flickr_key = get_secret("FLICKR_API_KEY", "")

    if flickr_key:
        url = "https://api.flickr.com/services/rest/"
        params = {
            "method": "flickr.photos.search",
            "api_key": flickr_key,
            "text": query,
            "is_commons": "true",
            "extras": "url_o,url_l,url_c,url_m",
            "per_page": 6,
            "format": "json",
            "nojsoncallback": 1
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=12)
            photos = r.json().get("photos", {}).get("photo", [])
            for p in photos:
                img_url = p.get("url_o") or p.get("url_l") or p.get("url_c") or p.get("url_m")
                if img_url:
                    ok, detail = download_stream(img_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
                    if ok:
                        return True, detail, img_url
        except Exception:
            pass

    try:
        tag_q = re.sub(r"\s+", ",", query.strip())
        feed_url = f"https://api.flickr.com/services/feeds/photos_public.gne?tags={tag_q}&tagmode=any&format=json&nojsoncallback=1"
        r = requests.get(feed_url, headers=headers, timeout=12)
        items = r.json().get("items", [])
        for item in items:
            media_m = item.get("media", {}).get("m", "")
            if media_m:
                high_res = media_m.replace("_m.jpg", "_b.jpg")
                ok, detail = download_stream(high_res, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
                if not ok:
                    ok, detail = download_stream(media_m, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
                    high_res = media_m
                if ok:
                    return True, detail, high_res
        return False, "No matching vintage photos found in Flickr Commons", None
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
    "Library of Congress (Historic Photos)": fetch_loc_photo,
    "National Archives (NARA Historical Footage)": fetch_nara_video,
    "National Archives (NARA Historical Stills)": fetch_nara_photo,
    "Internet Archive (Prelinger & Historic Video)": fetch_ia_video,
    "Internet Archive (Vintage Stills & Scans)": fetch_ia_photo,
    "Flickr Commons (Global Cultural Heritage)": fetch_flickr_commons
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
    st.caption("High-speed B-roll, public domain & archival pipeline for documentary research.")
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

# YouTube Transcript State Cache
if "yt_transcript_data" not in st.session_state:
    st.session_state.yt_transcript_data = []
if "yt_search_matches" not in st.session_state:
    st.session_state.yt_search_matches = []
if "yt_active_video_id" not in st.session_state:
    st.session_state.yt_active_video_id = ""

# =====================================================================
# AUTHENTICATED WORKSPACE
# =====================================================================
col_header, col_logout = st.columns([4, 1])
with col_header:
    st.markdown("# 🎬 **Automation Tools By Shoaib Malik**")
    st.caption("⚡ Modern Stock + Public Domain Archives + YouTube Transcript Trimmer")
with col_logout:
    st.write("")
    if st.button("🔒 **Log Out**", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.batch_results = []
        st.session_state.zip_bytes = None
        st.session_state.yt_transcript_data = []
        st.session_state.yt_search_matches = []
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
    st.session_state.last_tool_used = selected_tool_name

with col_main:
    st.markdown(f"### **Tool: {selected_tool_name}**")
    st.info(tool_info["desc"])

    # =================================================================
    # TOOL A: YOUTUBE TRANSCRIPT PRECISION TRIMMER
    # =================================================================
    if tool_info["type"] == "transcript_cutter":
        st.markdown("#### **Step 1: Locate Video & Search Dialogue**")
        yt_url_input = st.text_input(
            "YouTube Video URL:",
            placeholder="https://www.youtube.com/watch?v=..."
        )

        col_yt_q, col_yt_btn = st.columns([2.5, 1.2])
        with col_yt_q:
            yt_keyword = st.text_input(
                "Dialogue / Keyword to Find in Transcript:",
                placeholder="e.g. artificial intelligence, investigation, crime scene"
            )
        with col_yt_btn:
            st.write("")
            search_trans_btn = st.button("🔍 **Search Spoken Dialogue**", type="primary", use_container_width=True)

        if search_trans_btn:
            if not yt_url_input.strip():
                st.warning("Please provide a valid YouTube URL.")
            elif not yt_keyword.strip():
                st.warning("Please enter a dialogue search term or keyword.")
            else:
                v_id = extract_youtube_video_id(yt_url_input)
                if not v_id:
                    st.error("Invalid YouTube URL. Please verify the link.")
                else:
                    with st.spinner("Extracting video transcript and searching spoken audio..."):
                        if st.session_state.yt_active_video_id != v_id:
                            ok, trans_data = fetch_youtube_transcript_data(v_id)
                            if not ok:
                                st.error(f"Transcript extraction failed: {trans_data}")
                                st.session_state.yt_transcript_data = []
                            else:
                                st.session_state.yt_transcript_data = trans_data
                                st.session_state.yt_active_video_id = v_id

                    if st.session_state.yt_transcript_data:
                        query_words = yt_keyword.strip().lower().split()
                        matches = []
                        for idx, entry in enumerate(st.session_state.yt_transcript_data):
                            txt = entry.get("text", "")
                            if any(w in txt.lower() for w in query_words):
                                start = entry.get("start", 0.0)
                                dur = entry.get("duration", 0.0)
                                end = start + max(dur, 4.0)
                                matches.append({
                                    "index": idx,
                                    "start": start,
                                    "end": end,
                                    "start_str": format_seconds_to_timestamp(start),
                                    "end_str": format_seconds_to_timestamp(end),
                                    "text": txt
                                })
                        st.session_state.yt_search_matches = matches

                        if not matches:
                            st.warning(f"No spoken dialogue matching '{yt_keyword}' was found in this transcript.")
                        else:
                            st.success(f"✓ Found {len(matches)} spoken match(es) across this video transcript!")

        # Render dialogue matches and trim panel
        if st.session_state.yt_search_matches:
            st.markdown("---")
            st.markdown("#### **Step 2: Choose Spoken Occurrence to Trim**")

            options = [
                f"[{m['start_str']} - {m['end_str']}] {m['text'][:85]}..."
                for m in st.session_state.yt_search_matches
            ]
            selected_idx = st.selectbox("Select Occurrence:", range(len(options)), format_func=lambda x: options[x])
            chosen_match = st.session_state.yt_search_matches[selected_idx]

            col_ts1, col_ts2, col_pad = st.columns([1.2, 1.2, 1])
            with col_pad:
                padding_sec = st.number_input("Padding (+/- sec):", min_value=0, max_value=15, value=2, step=1)

            cal_start = max(0.0, chosen_match["start"] - padding_sec)
            cal_end = chosen_match["end"] + padding_sec

            with col_ts1:
                start_ts_input = st.text_input("Start Timestamp:", value=format_seconds_to_timestamp(cal_start))
            with col_ts2:
                end_ts_input = st.text_input("End Timestamp:", value=format_seconds_to_timestamp(cal_end))

            col_q, col_audio = st.columns([1.5, 1])
            with col_q:
                yt_quality = st.selectbox("Target Resolution:", ["1080p Full HD", "4K UHD (2160p)", "720p HD"], index=0)
            with col_audio:
                st.write("")
                mute_audio_val = st.checkbox("Mute Audio (Silent B-roll)", value=False)

            clip_title = st.text_input("Clip Title / Filename:", value=f"yt_clip_{chosen_match['start_str'].replace(':', '_')}")

            if st.button("✂️ **Trim & Download This Clip Now**", type="primary", use_container_width=True):
                s_sec = time_to_seconds(start_ts_input)
                e_sec = time_to_seconds(end_ts_input)

                if s_sec is None or e_sec is None or (e_sec - s_sec) <= 0:
                    st.error("Invalid timestamps. End timestamp must be greater than start timestamp.")
                else:
                    clean_name = prompt_to_clean_filename(clip_title, "mp4")
                    final_clip_path = os.path.join(OUTPUT_DIR, clean_name)

                    with st.spinner("Extracting precision segment from YouTube stream..."):
                        t0 = time.time()
                        ok_trim, trim_msg = trim_youtube_stream_slice(
                            yt_url_input, s_sec, e_sec, final_clip_path, yt_quality, mute_audio_val
                        )
                        elapsed_trim = time.time() - t0

                    if ok_trim and os.path.exists(final_clip_path):
                        st.success(f"✓ Clip extracted successfully: `{clean_name}` ({trim_msg} in {elapsed_trim:.1f}s)")

                        with open(final_clip_path, "rb") as cf:
                            st.download_button(
                                label=f"⬇️ **Download {clean_name} (.MP4)**",
                                data=cf.read(),
                                file_name=clean_name,
                                mime="video/mp4",
                                type="primary",
                                use_container_width=True
                            )

                        st.write("---")
                        st.video(final_clip_path)
                    else:
                        st.error(f"✖ Trimming failed: {trim_msg}")

    # =================================================================
    # TOOL B: REPOSITORY SEARCH & BATCH SOURCING
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
            placeholder="wright brothers first flight kitty hawk\ncivil war battlefield photography\n1930s great depression street scene",
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
