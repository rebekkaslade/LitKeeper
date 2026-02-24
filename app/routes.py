from flask import Blueprint, request, render_template, send_from_directory, jsonify, abort, current_app
from .utils import download_story, create_epub_file, create_pdf_file, log_error, log_action, log_url, send_notification
import os
from datetime import datetime
import traceback
import urllib.parse
from threading import Thread
import uuid
import json

# Directory to store job status files
JOBS_DIR = os.path.join(os.path.dirname(__file__), "data", "jobs")

def ensure_jobs_dir():
    try:
        os.makedirs(JOBS_DIR, exist_ok=True)
    except Exception:
        log_action(f"Failed to create jobs dir: {JOBS_DIR}")

def write_job(jobid, payload):
    ensure_jobs_dir()
    path = os.path.join(JOBS_DIR, f"{jobid}.json")
    with open(path, 'w') as f:
        json.dump(payload, f)

def read_job(jobid):
    path = os.path.join(JOBS_DIR, f"{jobid}.json")
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)

def update_job(jobid, **kwargs):
    data = read_job(jobid) or {}
    data.update(kwargs)
    write_job(jobid, data)

# Blueprint for module routing
main = Blueprint('main', __name__)

def background_process_url(app, url):
    """Process URL in background without returning JSON response."""
    try:
        with app.app_context():
            # Download the story and generate the EPUB
            log_action("Starting story download")
            story_content, story_title, story_author, story_category, story_tags = download_story(url)
            if not story_content:
                error_msg = f"Failed to download the story from the given URL: {url}"
                log_error(error_msg, url)
                log_action(f"Download failed: {error_msg}")
                send_notification(f"Story download failed: {url}", is_error=True)
                return

            log_action(f"Successfully downloaded story: '{story_title}' by {story_author}")
            log_action("Starting EPUB creation")

            epub_file_name = create_epub_file(
                story_title, 
                story_author, 
                story_content, 
                os.path.join(os.path.dirname(__file__), "data", "epubs"),
                story_category=story_category,
                story_tags=story_tags
            )
            log_action(f"Successfully created EPUB file: {epub_file_name}")
            send_notification(f"Story downloaded successfully: '{story_title}' by {story_author}")

    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        log_error(error_msg, url)
        log_action(f"Error occurred: {str(e)}")
        send_notification(f"Error processing story: {str(e)}", is_error=True)


def background_process_job(app, url, jobid, fmt='epub'):
    """Background worker that updates job status file as it progresses."""
    try:
        with app.app_context():
            update_job(jobid, status='processing', started_at=datetime.utcnow().isoformat())
            log_action(f"[job:{jobid}] Starting story download")
            story_content, story_title, story_author, story_category, story_tags = download_story(url)
            if not story_content:
                error_msg = f"Failed to download the story from the given URL: {url}"
                log_error(error_msg, url)
                update_job(jobid, status='failed', error=error_msg, finished_at=datetime.utcnow().isoformat())
                send_notification(f"Story download failed: {url}", is_error=True)
                return

            log_action(f"[job:{jobid}] Successfully downloaded story: '{story_title}' by {story_author}")
            update_job(jobid, status='creating', title=story_title, author=story_author)

            # Create file according to requested format
            if fmt and fmt.lower() == 'pdf':
                output_path = create_pdf_file(
                    story_title,
                    story_author,
                    story_content,
                    os.path.join(os.path.dirname(__file__), "data", "epubs"),
                    story_category=story_category,
                    story_tags=story_tags
                )
            else:
                output_path = create_epub_file(
                    story_title,
                    story_author,
                    story_content,
                    os.path.join(os.path.dirname(__file__), "data", "epubs"),
                    story_category=story_category,
                    story_tags=story_tags
                )

            if output_path and os.path.exists(output_path):
                base_filename = os.path.basename(output_path)
                update_job(jobid, status='done', saved_as=base_filename, finished_at=datetime.utcnow().isoformat())
                log_action(f"[job:{jobid}] Successfully created output file: {output_path}")
                send_notification(f"Story downloaded successfully: '{story_title}' by {story_author}")
            else:
                error_msg = f"Failed to create output for job {jobid}"
                update_job(jobid, status='failed', error=error_msg, finished_at=datetime.utcnow().isoformat())
                log_error(error_msg, url)
                send_notification(f"Output creation failed for job {jobid}", is_error=True)

    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        log_error(error_msg, url)
        update_job(jobid, status='failed', error=error_msg, finished_at=datetime.utcnow().isoformat())
        log_action(f"[job:{jobid}] Error occurred: {str(e)}")
        send_notification(f"Error processing story: {str(e)}", is_error=True)

@main.route("/api/download", methods=['GET', 'POST'])
def api_download():
    """API endpoint for iOS shortcuts to trigger downloads."""
    # Log request details for debugging
    log_action(f"API Request Method: {request.method}")
    log_action(f"Request Headers: {dict(request.headers)}")
    
    if request.method == 'POST':
        log_action(f"POST Raw Data: {request.get_data(as_text=True)}")
        
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json()
            url = data.get('url')
            wait = data.get('wait', True)
            fmt = data.get('format', 'epub')
            if isinstance(wait, str):
                wait = wait.lower() == 'true'
        else:
            log_action(f"POST Form Data: {dict(request.form)}")
            url = request.form.get('url')
            wait = request.form.get('wait', 'true').lower() == 'true'
            fmt = request.form.get('format', 'epub')
    else:  # GET
        log_action(f"GET Query Parameters: {dict(request.args)}")
        url = request.args.get('url')
        wait = request.args.get('wait', 'true').lower() == 'true'
        fmt = request.args.get('format', 'epub')

    if not url:
        error_msg = "API request received without URL parameter"
        log_error(f"{error_msg}\nRequest Method: {request.method}\nHeaders: {dict(request.headers)}\nData: {request.get_data(as_text=True)}")
        return jsonify({
            "success": "false",
            "message": error_msg
        }), 400

    # Clean the URL: remove whitespace, newlines, and decode
    url = url.strip()  # Remove leading/trailing whitespace
    url = url.split()[0]  # Take only the first URL if multiple are provided
    url = urllib.parse.unquote(url)  # URL decode
    
    log_action(f"API request received for URL: {url}")
    
    # Log URL once at the entry point
    log_url(url)
    
    # Check if URL is from allowed domain
    if not url.startswith("https://www.literotica.com/"):
        error_msg = f"Invalid URL domain: {url}"
        log_error(error_msg, url)
        return jsonify({
            "success": "false",
            "message": error_msg
        }), 400

    if not wait:
        # Create a job id and store initial job state
        jobid = uuid.uuid4().hex
        created_at = datetime.utcnow().isoformat()
        job_payload = {
            "job_id": jobid,
            "status": "pending",
            "url": url,
            "created_at": created_at
        }
        write_job(jobid, job_payload)

        # Get the current app context
        app = current_app._get_current_object()
        # Start processing in background thread (job-aware)
        thread = Thread(target=background_process_job, args=(app, url, jobid, fmt))
        thread.start()
        return jsonify({
            "success": "true",
            "message": "Request accepted, processing in background",
            "job_id": jobid
        })

    return process_url(url, fmt)

def process_url(url, fmt='epub'):
    """Process the URL and create EPUB file."""
    try:
        # Download the story and generate the EPUB
        log_action("Starting story download")
        story_content, story_title, story_author, story_category, story_tags = download_story(url)
        if not story_content:
            error_msg = f"Failed to download the story from the given URL: {url}"
            log_error(error_msg, url)
            log_action(f"Download failed: {error_msg}")
            send_notification(f"Story download failed: {url}", is_error=True)
            return jsonify({
                "success": "false",
                "message": error_msg
            })

        log_action(f"Successfully downloaded story: '{story_title}' by {story_author}")
        log_action("Starting EPUB creation")

        if fmt and fmt.lower() == 'pdf':
            output_file = create_pdf_file(
                story_title,
                story_author,
                story_content,
                os.path.join(os.path.dirname(__file__), "data", "epubs"),
                story_category=story_category,
                story_tags=story_tags
            )
        else:
            output_file = create_epub_file(
                story_title,
                story_author,
                story_content,
                os.path.join(os.path.dirname(__file__), "data", "epubs"),
                story_category=story_category,
                story_tags=story_tags
            )
        log_action(f"Successfully created output file: {output_file}")
        send_notification(f"Story downloaded successfully: '{story_title}' by {story_author}")

        # Get the base filename without path
        base_filename = os.path.basename(output_file)

        return jsonify({
            "success": "true",
            "message": f"Successfully downloaded '{story_title}' by {story_author}",
            "title": story_title,
            "author": story_author,
            "saved_as": base_filename
        })
    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        log_error(error_msg, url)
        log_action(f"Error occurred: {str(e)}")
        send_notification(f"Error processing story: {str(e)}", is_error=True)
        return jsonify({
            "success": "false",
            "message": str(e)
        })

@main.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form.get("url")
        fmt = request.form.get('format', 'epub')
        return process_url(url, fmt)

    log_action("Serving index page")
    return render_template("index.html")

@main.route("/download/<filename>")
def download_file(filename):
    """Download a specific EPUB file."""
    # Basic security check: ensure filename doesn't contain path traversal
    if '..' in filename or filename.startswith('/'):
        log_action(f"Attempted path traversal in download: {filename}")
        abort(404)
        
    output_directory = os.path.join(os.path.dirname(__file__), "data", "epubs")
    log_action(f"Download requested for file: {filename}")
    return send_from_directory(output_directory, filename, as_attachment=True)


@main.route('/job/<jobid>/status', methods=['GET'])
def job_status(jobid):
    job = read_job(jobid)
    if not job:
        return jsonify({"success": "false", "message": "Job not found"}), 404
    return jsonify(job)


@main.route('/job/<jobid>/result', methods=['GET'])
def job_result(jobid):
    job = read_job(jobid)
    if not job:
        return jsonify({"success": "false", "message": "Job not found"}), 404
    if job.get('status') != 'done':
        return jsonify({"success": "false", "message": "Job not completed", "status": job.get('status')}), 400
    # Return download URL for client convenience
    filename = job.get('saved_as')
    if not filename:
        return jsonify({"success": "false", "message": "No output file recorded"}), 500
    download_url = f"/download/{urllib.parse.quote(filename)}"
    return jsonify({"success": "true", "download_url": download_url, "saved_as": filename})