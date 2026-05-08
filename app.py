from flask import Flask, render_template, request, send_file, jsonify, Response, after_this_request
import yt_dlp
import instaloader
import os
import shutil
import tempfile
import zipfile
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, "downloads")

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)


MEDIA_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov")
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.instagram.com/",
}


def get_shortcode(url):
    clean_url = url.split("?")[0].split("#")[0].strip().rstrip("/")
    return clean_url.split("/")[-1]


def safe_name(value, fallback="instagram_post"):
    cleaned = "".join(
        char for char in value
        if char.isalnum() or char in ("-", "_")
    )
    return cleaned or fallback


def shorten_text(value, fallback, limit=90):
    text = " ".join((value or "").split()) or fallback

    if len(text) <= limit:
        return text

    return f"{text[:limit - 3]}..."


def proxied_media_url(media_url):
    if not media_url:
        return None

    return f"/media-proxy?url={quote(media_url, safe='')}"


def media_extension(media_type):
    return ".mp4" if media_type == "video" else ".jpg"


def get_video_url(item):
    try:
        return item.video_url
    except Exception:
        return None


def get_post_media_items(post):
    items = []

    if getattr(post, "typename", "") == "GraphSidecar":
        for index, node in enumerate(post.get_sidecar_nodes(), start=1):
            is_video = bool(getattr(node, "is_video", False))
            thumbnail = getattr(node, "display_url", None)
            media_url = get_video_url(node) if is_video else thumbnail

            items.append({
                "index": index,
                "media_type": "video" if is_video else "image",
                "thumbnail": thumbnail,
                "download_url": media_url,
            })
    else:
        is_video = bool(getattr(post, "is_video", False))
        thumbnail = getattr(post, "url", None)
        media_url = get_video_url(post) if is_video else thumbnail

        items.append({
            "index": 1,
            "media_type": "video" if is_video else "image",
            "thumbnail": thumbnail,
            "download_url": media_url,
        })

    return items


def preview_from_post(url):
    shortcode = get_shortcode(url)
    loader = instaloader.Instaloader(quiet=True)
    post = instaloader.Post.from_shortcode(loader.context, shortcode)
    items = get_post_media_items(post)

    if not items:
        raise ValueError("No media found in post.")

    owner = getattr(post, "owner_username", "Instagram")
    title = shorten_text(
        getattr(post, "caption", ""),
        f"Instagram Post by {owner}"
    )
    clean_items = []

    for item in items:
        clean_items.append({
            "index": item["index"],
            "media_type": item["media_type"],
            "thumbnail": proxied_media_url(item["thumbnail"]),
            "label": f"Item {item['index']}",
        })

    return {
        "success": True,
        "source": "instaloader",
        "shortcode": shortcode,
        "owner": owner,
        "title": title,
        "kind": "carousel" if len(clean_items) > 1 else clean_items[0]["media_type"],
        "count": len(clean_items),
        "items": clean_items,
    }


def preview_from_ytdlp(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    thumbnails = info.get("thumbnails") or []
    thumbnail = thumbnails[-1].get("url") if thumbnails else None
    thumbnail = thumbnail or info.get("thumbnail")
    media_type = "video" if "/reel/" in url or info.get("ext") == "mp4" else "image"

    return {
        "success": True,
        "source": "yt_dlp",
        "shortcode": get_shortcode(url),
        "owner": info.get("uploader") or "Instagram",
        "title": shorten_text(info.get("title"), "Instagram Media"),
        "kind": media_type,
        "count": 1,
        "items": [{
            "index": 1,
            "media_type": media_type,
            "thumbnail": proxied_media_url(thumbnail),
            "label": "Item 1",
        }],
    }


def find_media_files(folder):
    media_files = []

    for root, _, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(MEDIA_EXTENSIONS):
                media_files.append(os.path.join(root, file))

    return sorted(media_files)


def download_remote_file(media_url, destination):
    if not media_url:
        raise ValueError("Media URL is missing.")

    request = Request(media_url, headers=REQUEST_HEADERS)

    with urlopen(request, timeout=90) as response:
        with open(destination, "wb") as file:
            shutil.copyfileobj(response, file)


def create_zip(source_files, source_folder, zip_name):
    zip_path = os.path.join(source_folder, zip_name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in source_files:
            zip_file.write(
                file_path,
                arcname=os.path.relpath(file_path, source_folder)
            )

    return zip_path


def selected_file_from_downloaded_post(post, loader, post_dir, safe_shortcode, selection):
    loader.download_post(post, target=safe_shortcode)
    media_files = find_media_files(post_dir)

    if not media_files:
        return None, None

    if selection == "all":
        if len(media_files) == 1:
            file_path = media_files[0]
            extension = os.path.splitext(file_path)[1]
            return file_path, f"instagram_{safe_shortcode}{extension}"

        download_name = f"instagram_{safe_shortcode}.zip"
        return (
            create_zip(media_files, post_dir, download_name),
            download_name,
        )

    selected_index = int(selection)

    if selected_index < 1 or selected_index > len(media_files):
        raise ValueError("Selected media item is not available.")

    file_path = media_files[selected_index - 1]
    extension = os.path.splitext(file_path)[1]
    return (
        file_path,
        f"instagram_{safe_shortcode}_{selected_index:02d}{extension}",
    )


def download_post_with_instaloader(url, selection="all"):
    shortcode = get_shortcode(url)
    safe_shortcode = safe_name(shortcode)
    post_dir = tempfile.mkdtemp(
        prefix=f"{safe_shortcode}_",
        dir=DOWNLOAD_FOLDER
    )

    try:
        loader = instaloader.Instaloader(
            dirname_pattern=post_dir,
            save_metadata=False,
            download_comments=False,
            download_video_thumbnails=False,
            quiet=True
        )

        post = instaloader.Post.from_shortcode(
            loader.context,
            shortcode
        )
        media_items = get_post_media_items(post)

        if selection != "all":
            try:
                selected_index = int(selection)
            except ValueError:
                return "Invalid media selection.", 400

            if selected_index < 1 or selected_index > len(media_items):
                return "Selected media item is not available.", 400

            item = media_items[selected_index - 1]
            extension = media_extension(item["media_type"])
            download_name = f"instagram_{safe_shortcode}_{selected_index:02d}{extension}"
            file_path = os.path.join(post_dir, download_name)

            try:
                download_remote_file(item["download_url"], file_path)
            except Exception as direct_error:
                print("Direct media download failed, using Instaloader:", direct_error)

                if os.path.exists(file_path):
                    os.remove(file_path)

                file_path, download_name = selected_file_from_downloaded_post(
                    post,
                    loader,
                    post_dir,
                    safe_shortcode,
                    selection
                )
        else:
            file_path, download_name = selected_file_from_downloaded_post(
                post,
                loader,
                post_dir,
                safe_shortcode,
                selection
            )

        if not file_path:
            shutil.rmtree(post_dir, ignore_errors=True)
            return "Failed to find downloaded post media.", 500

        @after_this_request
        def cleanup_post(response):
            try:
                shutil.rmtree(post_dir, ignore_errors=True)
            except Exception:
                pass
            return response

        return send_file(
            file_path,
            as_attachment=True,
            download_name=download_name
        )

    except Exception:
        shutil.rmtree(post_dir, ignore_errors=True)
        raise


# HOME PAGE
@app.route("/")
def home():
    return render_template("index.html")


# PREVIEW MEDIA PROXY
@app.route("/media-proxy")
def media_proxy():

    try:
        media_url = request.args.get("url", "").strip()
        parsed_url = urlparse(media_url)

        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            return "Invalid media URL.", 400

        proxy_request = Request(media_url, headers=REQUEST_HEADERS)

        with urlopen(proxy_request, timeout=45) as response:
            content = response.read()
            content_type = response.headers.get("Content-Type", "image/jpeg")

        proxied_response = Response(content, content_type=content_type)
        proxied_response.headers["Cache-Control"] = "public, max-age=900"
        return proxied_response

    except Exception as e:
        print("MEDIA PROXY ERROR:", e)
        return "Failed to load media.", 502


# PREVIEW ROUTE
@app.route("/preview", methods=["POST"])
def preview():

    try:
        payload = request.get_json(silent=True) or {}
        url = (request.form.get("url") or payload.get("url", "")).strip()

        if not url:
            return jsonify({
                "success": False,
                "error": "Missing Instagram URL."
            }), 400

        try:
            return jsonify(preview_from_post(url))
        except Exception as post_error:
            print("Instaloader preview failed, trying yt-dlp:", post_error)
            return jsonify(preview_from_ytdlp(url))

    except Exception as e:
        print("PREVIEW ERROR:", e)
        return jsonify({
            "success": False,
            "error": "Failed to load preview."
        }), 500


# DOWNLOAD ROUTE
@app.route("/download", methods=["POST"])
def download():

    try:

        payload = request.get_json(silent=True) or {}
        url = (request.form.get("url") or payload.get("url", "")).strip()
        selection = str(
            request.form.get("selection") or payload.get("selection") or "all"
        ).strip().lower()

        if not url:
            return "Missing Instagram URL.", 400

        if selection != "all" or "/p/" in url:
            return download_post_with_instaloader(url, selection)

        # VIDEO / REELS
        try:

            ydl_opts = {
                "format": "mp4",
                "outtmpl": os.path.join(
                    DOWNLOAD_FOLDER,
                    "%(title)s.%(ext)s"
                ),
                "quiet": True,
                "no_warnings": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:

                info = ydl.extract_info(
                    url,
                    download=True
                )

                file_path = ydl.prepare_filename(info)

            @after_this_request
            def cleanup_video(response):
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception:
                    pass
                return response

            return send_file(
                file_path,
                as_attachment=True
            )

        except Exception as yt_err:
            print("YT-DLP download failed, trying Instaloader:", yt_err)
            return download_post_with_instaloader(url, selection)

    except Exception as e:

        print("DOWNLOAD ERROR:", e)

        return "Failed to download.", 500


if __name__ == "__main__":
    app.run(debug=True)
