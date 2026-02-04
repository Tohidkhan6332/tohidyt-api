import os
import tempfile
import time
import requests
from http.cookiejar import MozillaCookieJar
from flask import Flask, request, jsonify, make_response
from flask_caching import Cache
from youtube_search import YoutubeSearch
import yt_dlp


# -------------------------
# ✅ Tohid Branding (tohidyt-api)
# -------------------------
TOHID_API_NAME = "tohidyt-api"
TOHID_AUTHOR = "Mr Tohid"
TOHID_POWERED_BY = "Powered by Tohid"

# ✅ Private API Key (Set in Vercel Environment Variables)
TOHID_API_KEY = os.environ.get("TOHID_API_KEY", "").strip()

# ✅ Allowed frontends/domains (Set in env: TOHID_ALLOWED_ORIGINS)
# Example: https://tohid.vercel.app,https://tohidtech.in
allowed_origins_env = os.environ.get("TOHID_ALLOWED_ORIGINS", "").strip()
TOHID_ALLOWED_ORIGINS = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]


# -------------------------
# Temp Directory (Vercel Compatible)
# -------------------------
temp_dir = os.environ.get("TMPDIR", tempfile.gettempdir())
cookie_file = os.path.join(temp_dir, "cookies.txt")
cookies_file = cookie_file


# -------------------------
# Load Cookies and Patch requests.get
# -------------------------
if os.path.exists(cookie_file):
    cookie_jar = MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session()
    session.cookies = cookie_jar
    original_get = requests.get

    def get_with_cookies(url, **kwargs):
        kwargs.setdefault("cookies", session.cookies)
        return original_get(url, **kwargs)

    requests.get = get_with_cookies


# -------------------------
# Flask App
# -------------------------
app = Flask(__name__)


# -------------------------
# Cache
# -------------------------
cache = Cache(app, config={
    "CACHE_TYPE": "simple",
    "CACHE_DEFAULT_TIMEOUT": 0
})


# -------------------------
# ✅ Base JSON Wrapper (auto branding)
# -------------------------
def tohid_json(payload: dict, code: int = 200):
    payload.update({
        "api": TOHID_API_NAME,
        "author": TOHID_AUTHOR,
        "powered_by": TOHID_POWERED_BY
    })
    return jsonify(payload), code


# -------------------------
# ✅ CORS + Origin Allowlist
# -------------------------
def tohid_apply_cors(resp):
    origin = request.headers.get("Origin")

    # If allowlist not set -> deny all browser origins (private)
    if TOHID_ALLOWED_ORIGINS:
        if origin in TOHID_ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        else:
            # No CORS = browser blocked (still API works for server/server)
            pass
    return resp


@app.after_request
def after_request(resp):
    resp = tohid_apply_cors(resp)

    # Always send these
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, x-api-key, Authorization"
    resp.headers["Access-Control-Max-Age"] = "86400"

    return resp


@app.route("/<path:any_path>", methods=["OPTIONS"])
def cors_preflight(any_path):
    resp = make_response("", 204)
    resp = tohid_apply_cors(resp)
    return resp


# -------------------------
# ✅ Rate Limit (in-memory)
# -------------------------
RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW = 60
rate_store = {}


def tohid_rate_limit():
    ip = request.headers.get("x-forwarded-for", request.remote_addr) or "unknown"
    endpoint = request.path
    key = f"{ip}:{endpoint}"
    now = time.time()

    timestamps = rate_store.get(key, [])
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]

    if len(timestamps) >= RATE_LIMIT_MAX:
        return tohid_json({
            "error": "Too many requests",
            "limit": RATE_LIMIT_MAX,
            "window_seconds": RATE_LIMIT_WINDOW
        }, 429)

    timestamps.append(now)
    rate_store[key] = timestamps
    return None


# -------------------------
# ✅ API Key Protection (Private API)
# -------------------------
def tohid_private_access():
    # allow these without key
    if request.path in ["/", "/api/status"]:
        return None

    if not TOHID_API_KEY:
        return tohid_json({
            "error": "Server is missing TOHID_API_KEY env variable"
        }, 500)

    key = (
        request.headers.get("x-api-key")
        or request.args.get("apikey")
        or request.headers.get("authorization")
    )

    if not key:
        return tohid_json({
            "error": "Private API - missing API key",
            "hint": "Send x-api-key header or ?apikey=YOUR_KEY"
        }, 401)

    # If authorization: Bearer KEY
    if key.lower().startswith("bearer "):
        key = key.split(" ", 1)[1].strip()

    if key != TOHID_API_KEY:
        return tohid_json({"error": "Invalid API key"}, 403)

    return None


# -------------------------
# ✅ Global Middleware
# -------------------------
@app.before_request
def before_request():
    if request.path == "/favicon.ico":
        return None

    # Private Access Check
    access = tohid_private_access()
    if access:
        return access

    # Rate limit
    if request.path.startswith("/api") or request.path.startswith("/download"):
        blocked = tohid_rate_limit()
        if blocked:
            return blocked


# -------------------------
# Helper: Convert durations to ISO 8601
# -------------------------
def to_iso_duration(duration_str: str) -> str:
    parts = duration_str.split(":") if duration_str else []
    iso = "PT"
    if len(parts) == 3:
        h, m, s = parts
        if int(h):
            iso += f"{int(h)}H"
        iso += f"{int(m)}M{int(s)}S"
    elif len(parts) == 2:
        m, s = parts
        iso += f"{int(m)}M{int(s)}S"
    elif len(parts) == 1 and parts[0].isdigit():
        iso += f"{int(parts[0])}S"
    else:
        iso += "0S"
    return iso


# -------------------------
# yt-dlp Options
# -------------------------
ydl_opts_full = {
    "quiet": True,
    "skip_download": True,
    "format": "bestvideo+bestaudio/best",
    "cookiefile": cookies_file,
    "cachedir": False
}

ydl_opts_meta = {
    "quiet": True,
    "skip_download": True,
    "simulate": True,
    "noplaylist": True,
    "cookiefile": cookies_file
}


def extract_info(url=None, search_query=None, opts=None):
    ydl_opts = opts or ydl_opts_full
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        if search_query:
            result = ydl.extract_info(f"ytsearch:{search_query}", download=False)
            entries = result.get("entries")
            if not entries:
                return None, {"error": "No search results"}, 404
            return entries[0], None, None
        else:
            info = ydl.extract_info(url, download=False)
            return info, None, None


# -------------------------
# Format Helpers
# -------------------------
def get_size_bytes(fmt):
    return fmt.get("filesize") or fmt.get("filesize_approx") or 0


def format_size(bytes_val):
    if bytes_val >= 1e9:
        return f"{bytes_val/1e9:.2f} GB"
    if bytes_val >= 1e6:
        return f"{bytes_val/1e6:.2f} MB"
    if bytes_val >= 1e3:
        return f"{bytes_val/1e3:.2f} KB"
    return f"{bytes_val} B"


def build_formats_list(info):
    fmts = []
    for f in info.get("formats", []):
        url_f = f.get("url")
        if not url_f:
            continue

        has_video = f.get("vcodec") != "none"
        has_audio = f.get("acodec") != "none"

        kind = (
            "progressive" if has_video and has_audio else
            "video-only" if has_video else
            "audio-only" if has_audio else None
        )

        if not kind:
            continue

        size = get_size_bytes(f)

        fmts.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "kind": kind,
            "filesize_bytes": size,
            "filesize": format_size(size),
            "width": f.get("width"),
            "height": f.get("height"),
            "fps": f.get("fps"),
            "abr": f.get("abr"),
            "asr": f.get("asr"),
            "url": url_f
        })
    return fmts


# -------------------------
# ✅ Routes
# -------------------------
@app.route("/")
def home():
    return tohid_json({"message": "✅ tohidyt-api is alive"})[0]


@app.route("/api/status")
def api_status():
    return tohid_json({
        "status": "ok",
        "private": True,
        "allowed_origins": TOHID_ALLOWED_ORIGINS,
        "rate_limit": {
            "max_requests": RATE_LIMIT_MAX,
            "window_seconds": RATE_LIMIT_WINDOW
        }
    })[0]


@app.route("/api/fast-meta")
def api_fast_meta():
    q = request.args.get("search", "").strip()
    u = request.args.get("url", "").strip()

    key = f"fast_meta:{q}:{u}"
    if "latest" in request.args:
        cache.delete(key)

    cached = cache.get(key)
    if cached is not None:
        return tohid_json(cached)[0]

    if not q and not u:
        return tohid_json({"error": 'Provide either "search" or "url" parameter'}, 400)

    try:
        result = None

        if q:
            results = YoutubeSearch(q, max_results=1).to_dict()
            if results:
                vid = results[0]
                result = {
                    "title": vid.get("title"),
                    "link": f"https://www.youtube.com/watch?v={vid.get('url_suffix').split('v=')[-1]}",
                    "duration": to_iso_duration(vid.get("duration", "")),
                    "thumbnail": vid.get("thumbnails", [None])[0]
                }
        else:
            with yt_dlp.YoutubeDL(ydl_opts_meta) as ydl:
                info = ydl.extract_info(u, download=False)
            result = {
                "title": info.get("title"),
                "link": info.get("webpage_url"),
                "duration": to_iso_duration(str(info.get("duration"))),
                "thumbnail": info.get("thumbnail")
            }

        if not result:
            return tohid_json({"error": "No results"}, 404)

        cache.set(key, result)
        return tohid_json(result)[0]

    except Exception as e:
        return tohid_json({"error": str(e)}, 500)


@app.route("/api/all")
def api_all():
    q = request.args.get("search", "").strip()
    u = request.args.get("url", "").strip()

    if not (q or u):
        return tohid_json({"error": 'Provide "url" or "search"'}, 400)

    info, err, code = extract_info(u or None, q or None)
    if err:
        return tohid_json(err, code)

    fmts = build_formats_list(info)

    suggestions = [
        {
            "id": rel.get("id"),
            "title": rel.get("title"),
            "url": rel.get("webpage_url") or rel.get("url"),
            "thumbnail": rel.get("thumbnails", [{}])[0].get("url")
        }
        for rel in info.get("related", [])
    ]

    data = {
        "title": info.get("title"),
        "video_url": info.get("webpage_url"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "thumbnail": info.get("thumbnail"),
        "description": info.get("description"),
        "tags": info.get("tags"),
        "is_live": info.get("is_live"),
        "age_limit": info.get("age_limit"),
        "average_rating": info.get("average_rating"),
        "channel": {
            "name": info.get("uploader"),
            "url": info.get("uploader_url") or info.get("channel_url"),
            "id": info.get("uploader_id")
        },
        "formats": fmts,
        "suggestions": suggestions
    }

    return tohid_json(data)[0]


@app.route("/api/meta")
def api_meta():
    q = request.args.get("search", "").strip()
    u = request.args.get("url", "").strip()

    key = f"meta:{q}:{u}"
    if "latest" in request.args:
        cache.delete(key)

    cached = cache.get(key)
    if cached:
        return tohid_json(cached)[0]

    if not (q or u):
        return tohid_json({"error": 'Provide "url" or "search"'}, 400)

    info, err, code = extract_info(u or None, q or None, opts=ydl_opts_meta)
    if err:
        return tohid_json(err, code)

    keys = [
        "id", "title", "webpage_url", "duration", "upload_date",
        "view_count", "like_count", "thumbnail", "description",
        "tags", "is_live", "age_limit", "average_rating",
        "uploader", "uploader_url", "uploader_id"
    ]

    data = {"metadata": {k: info.get(k) for k in keys}}
    cache.set(key, data)
    return tohid_json(data)[0]


@app.route("/download")
def api_download():
    url = request.args.get("url")
    search = request.args.get("search")

    if not (url or search):
        return tohid_json({"error": 'Provide "url" or "search"'}, 400)

    info, err, code = extract_info(url, search)
    if err:
        return tohid_json(err, code)

    return tohid_json({"formats": build_formats_list(info)})[0]


@app.route("/api/audio")
def api_audio():
    url = request.args.get("url")
    search = request.args.get("search")

    if not (url or search):
        return tohid_json({"error": 'Provide "url" or "search"'}, 400)

    info, err, code = extract_info(url, search)
    if err:
        return tohid_json(err, code)

    afmts = [f for f in build_formats_list(info) if f["kind"] in ("audio-only", "progressive")]
    return tohid_json({"audio_formats": afmts})[0]


@app.route("/api/video")
def api_video():
    url = request.args.get("url")
    search = request.args.get("search")

    if not (url or search):
        return tohid_json({"error": 'Provide "url" or "search"'}, 400)

    info, err, code = extract_info(url, search)
    if err:
        return tohid_json(err, code)

    vfmts = [f for f in build_formats_list(info) if f["kind"] in ("video-only", "progressive")]
    return tohid_json({"video_formats": vfmts})[0]
