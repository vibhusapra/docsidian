"""Docsidian web app — drop a PDF, get an Obsidian vault (Markdown + figures).

Run:
    .venv/bin/python webapp.py
Then open http://127.0.0.1:5001 in your browser.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile

from flask import Flask, render_template_string, request, send_file

from port import convert

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload cap

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Docsidian</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 640px;
         margin: 6vh auto; padding: 0 20px; line-height: 1.5; }
  h1 { margin-bottom: 4px; }
  p.sub { color: #888; margin-top: 0; }
  form { border: 1px solid #8884; border-radius: 12px; padding: 22px; margin-top: 24px; }
  label { display: block; font-weight: 600; margin: 14px 0 6px; }
  input[type=text], input[type=file] { width: 100%; padding: 10px; box-sizing: border-box;
         border: 1px solid #8886; border-radius: 8px; background: transparent; color: inherit; }
  button { margin-top: 20px; width: 100%; padding: 12px; font-size: 16px; font-weight: 600;
           border: 0; border-radius: 8px; background: #6c5ce7; color: #fff; cursor: pointer; }
  button:disabled { opacity: .6; cursor: progress; }
  #status { margin-top: 18px; white-space: pre-wrap; font-family: ui-monospace, monospace;
            font-size: 13px; color: #888; }
</style>
</head>
<body>
  <h1>Docsidian</h1>
  <p class="sub">Drop a PDF → download a ready-to-open Obsidian vault (Markdown + figures).</p>

  <form id="f">
    <label for="pdf">PDF file</label>
    <input id="pdf" name="pdf" type="file" accept="application/pdf,.pdf" required>

    <label for="title">Note title <span style="font-weight:400;color:#888">(optional)</span></label>
    <input id="title" name="title" type="text" placeholder="defaults to the file name">

    <button id="go" type="submit">Convert</button>
    <div id="status"></div>
  </form>

<script>
const f = document.getElementById('f');
const go = document.getElementById('go');
const status = document.getElementById('status');

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = new FormData(f);
  if (!data.get('pdf') || !data.get('pdf').name) { status.textContent = 'Choose a PDF first.'; return; }
  go.disabled = true; go.textContent = 'Converting…'; status.textContent = 'Extracting and rendering…';
  try {
    const res = await fetch('/convert', { method: 'POST', body: data });
    if (!res.ok) { status.textContent = '❌ ' + (await res.text()); return; }
    const blob = await res.blob();
    const name = (res.headers.get('X-Vault-Name') || 'vault') + '.zip';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
    status.textContent = '✅ ' + (res.headers.get('X-Summary') || 'Done') +
      '\\nDownloaded ' + name + ' — unzip it and open the folder as a vault in Obsidian.';
  } catch (err) {
    status.textContent = '❌ ' + err;
  } finally {
    go.disabled = false; go.textContent = 'Convert';
  }
});
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/convert")
def convert_endpoint():
    up = request.files.get("pdf")
    if not up or not up.filename:
        return "No PDF uploaded.", 400

    title = (request.form.get("title") or "").strip() or os.path.splitext(up.filename)[0]
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "vault"

    work = tempfile.mkdtemp(prefix="docsidian_")
    try:
        pdf_path = os.path.join(work, "input.pdf")
        up.save(pdf_path)

        out_dir = os.path.join(work, safe)
        res = convert(pdf_path, out_dir, title, to="obsidian")

        # Zip the vault (note + attachments) into memory for download.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(out_dir):
                for name in files:
                    full = os.path.join(root, name)
                    arc = os.path.join(safe, os.path.relpath(full, out_dir))
                    zf.write(full, arc)
        buf.seek(0)

        summary = (f"{res['pages']} pages · {res['figures']} figures · "
                   f"{res['tables']} tables")
        resp = send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"{safe}.zip")
        resp.headers["X-Summary"] = summary
        resp.headers["X-Vault-Name"] = safe
        return resp
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    print("Docsidian running at http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False)
