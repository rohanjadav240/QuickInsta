from flask import Flask, render_template, request, send_file, jsonify, after_this_request
import yt_dlp
import instaloader
import os
import shutil

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, "downloads")

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)


# HOME PAGE
@app.route("/")
def home():
    return render_template("index.html")


# PREVIEW ROUTE
@app.route("/preview", methods=["POST"])
def preview():

    try:

        url = request.json.get("url", "").strip()

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:

                info = ydl.extract_info(
                    url,
                    download=False
                )

                thumbnail = None

                thumbnails = info.get("thumbnails")

                if thumbnails:
                    thumbnail = thumbnails[-1]["url"]

                if not thumbnail:
                    thumbnail = info.get("thumbnail")

                title = info.get("title", "Instagram Media")

                return jsonify({
                    "success": True,
                    "thumbnail": thumbnail,
                    "title": title,
                    "url": url
                })
        except Exception as yt_err:
            # Fallback to instaloader for images
            print("YT-DLP Preview Error, falling back to Instaloader:", yt_err)
            clean_url = url.split("?")[0]
            shortcode = clean_url.strip("/").split("/")[-1]

            loader = instaloader.Instaloader(quiet=True)
            post = instaloader.Post.from_shortcode(loader.context, shortcode)

            return jsonify({
                "success": True,
                "thumbnail": post.url,
                "title": f"Instagram Post by {post.owner_username}",
                "url": url
            })

    except Exception as e:

        print("PREVIEW ERROR:", e)

        return jsonify({
            "success": False
        })


# DOWNLOAD ROUTE
@app.route("/download", methods=["POST"])
def download():

    try:

        url = request.form.get("url", "").strip()

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

        except Exception:

            # IMAGE POSTS
            clean_url = url.split("?")[0]

            shortcode = clean_url.strip("/").split("/")[-1]

            loader = instaloader.Instaloader(
                dirname_pattern=DOWNLOAD_FOLDER,
                save_metadata=False,
                download_comments=False,
                download_video_thumbnails=False,
                quiet=True
            )

            post = instaloader.Post.from_shortcode(
                loader.context,
                shortcode
            )

            loader.download_post(post, target=shortcode)

            target_dir = os.path.join(DOWNLOAD_FOLDER, shortcode)
            
            # Find the image file
            image_path = None
            if os.path.exists(target_dir):
                for file in os.listdir(target_dir):
                    if file.endswith(".jpg") or file.endswith(".png"):
                        image_path = os.path.join(target_dir, file)
                        break

            if image_path:
                @after_this_request
                def cleanup_image(response):
                    try:
                        if os.path.exists(target_dir):
                            shutil.rmtree(target_dir)
                    except Exception:
                        pass
                    return response

                return send_file(
                    image_path,
                    as_attachment=True
                )
            else:
                return "Failed to find downloaded image."

    except Exception as e:

        print("DOWNLOAD ERROR:", e)

        return "Failed to download."


if __name__ == "__main__":
    app.run(debug=True)