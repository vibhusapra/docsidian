"""Docsidian web app — drop a PDF, get an Obsidian vault (Markdown + figures).

Run:
    .venv/bin/python webapp.py
Then open http://127.0.0.1:5001 in your browser.
"""
from __future__ import annotations

import io
import json
import os
import queue
import secrets
import shutil
import tempfile
import threading
import time
import zipfile

from flask import Flask, Response, render_template_string, request, send_file

from port import convert

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload cap

# In-memory job registry. Conversion runs in a background thread and pushes
# progress events onto the job's queue; the browser streams them over SSE.
# NOTE: state lives in one process — run gunicorn with a single worker
# (multiple threads is fine). See render.yaml.
JOBS: dict[str, dict] = {}

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Docsidian — PDF to Obsidian</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  :root {
    --bg: #0b0b12; --card: rgba(22,22,33,.72); --line: rgba(255,255,255,.10);
    --ink: #e9e9f1; --mut: #9a9ab0; --accent: #7c5cff; --accent2: #21d4a8;
  }
  html, body { margin: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    color: var(--ink); background: var(--bg); line-height: 1.55;
    min-height: 100vh; overflow-x: hidden;
  }
  /* animated gradient mesh */
  body::before {
    content: ""; position: fixed; inset: -30vmax; z-index: -1;
    background:
      radial-gradient(40vmax 40vmax at 18% 12%, #7c5cff55, transparent 60%),
      radial-gradient(38vmax 38vmax at 86% 22%, #21d4a844, transparent 60%),
      radial-gradient(45vmax 45vmax at 60% 95%, #ff5ca833, transparent 60%);
    filter: blur(14px); animation: drift 22s ease-in-out infinite alternate;
  }
  @keyframes drift { to { transform: translate3d(2%, -3%, 0) rotate(8deg) scale(1.08); } }

  .wrap { max-width: 760px; margin: 0 auto; padding: 7vh 22px 10vh; }
  .badge { display:inline-flex; gap:8px; align-items:center; font-size:12px; letter-spacing:.5px;
    text-transform:uppercase; color:var(--mut); border:1px solid var(--line);
    padding:6px 12px; border-radius:999px; background:rgba(255,255,255,.03); }
  .badge .dot { width:7px; height:7px; border-radius:50%; background:var(--accent2);
    box-shadow:0 0 10px var(--accent2); }
  h1 { font-size: clamp(34px, 6vw, 52px); line-height:1.05; margin:18px 0 8px; letter-spacing:-1.2px; }
  h1 .grad { background:linear-gradient(100deg,var(--accent),var(--accent2)); -webkit-background-clip:text;
    background-clip:text; color:transparent; }
  .lede { color:var(--mut); font-size:18px; max-width:54ch; margin:0; }

  .card { margin-top:30px; background:var(--card); border:1px solid var(--line); border-radius:20px;
    padding:26px; backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
    box-shadow:0 24px 60px -28px rgba(0,0,0,.8); }

  .drop { border:1.5px dashed var(--line); border-radius:16px; padding:34px 20px; text-align:center;
    cursor:pointer; transition:.18s border-color,.18s background,.18s transform; }
  .drop:hover { border-color:var(--accent); background:rgba(124,92,255,.06); }
  .drop.over { border-color:var(--accent2); background:rgba(33,212,168,.08); transform:scale(1.01); }
  .drop .ico { font-size:34px; }
  .drop .big { font-weight:650; font-size:17px; margin-top:8px; }
  .drop .small { color:var(--mut); font-size:13px; margin-top:4px; }
  .drop.has { border-style:solid; border-color:var(--accent); background:rgba(124,92,255,.08); }
  #fname { font-weight:650; color:var(--accent2); }
  input[type=file] { display:none; }

  .title-row { margin-top:16px; }
  .title-row input { width:100%; padding:12px 14px; border-radius:12px; color:var(--ink);
    border:1px solid var(--line); background:rgba(255,255,255,.03); font-size:15px; }
  .title-row input::placeholder { color:#6a6a82; }

  button { margin-top:18px; width:100%; padding:15px; font-size:16px; font-weight:700; cursor:pointer;
    border:0; border-radius:13px; color:#fff; letter-spacing:.2px;
    background:linear-gradient(100deg,var(--accent),#9b7bff); transition:.16s transform,.16s filter; }
  button:hover:not(:disabled){ transform:translateY(-1px); filter:brightness(1.07); }
  button:disabled { opacity:.7; cursor:progress; }

  .progress { margin-top:16px; height:8px; border-radius:999px; overflow:hidden;
    background:rgba(255,255,255,.07); display:none; }
  .progress.show { display:block; }
  .progress .fill { height:100%; width:0%; border-radius:999px; transition:width .3s ease;
    background:linear-gradient(90deg,var(--accent),var(--accent2)); }
  #status { margin-top:12px; font-size:14px; min-height:20px; font-variant-numeric:tabular-nums; }
  #status.ok { color:var(--accent2); } #status.err { color:#ff6b8a; }
  .spin { display:inline-block; width:14px; height:14px; vertical-align:-2px; margin-right:8px;
    border:2px solid #ffffff44; border-top-color:#fff; border-radius:50%; animation:sp .7s linear infinite; }
  @keyframes sp { to { transform:rotate(360deg); } }

  .feat-h { margin:46px 0 4px; font-size:13px; letter-spacing:1px; text-transform:uppercase; color:var(--mut); }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-top:14px; }
  .feat { border:1px solid var(--line); border-radius:14px; padding:15px 16px; background:rgba(255,255,255,.02); }
  .feat .t { font-weight:650; font-size:15px; display:flex; gap:8px; align-items:center; }
  .feat .t .ck { color:var(--accent2); }
  .feat .d { color:var(--mut); font-size:13px; margin-top:5px; }
  .foot { margin-top:40px; color:#6a6a82; font-size:13px; text-align:center; }
  .foot code { color:var(--mut); background:rgba(255,255,255,.05); padding:2px 7px; border-radius:6px; }
</style>
</head>
<body>
<div class="wrap">
  <span class="badge"><span class="dot"></span> No LLM · runs on direct extraction</span>
  <h1>PDF in. <span class="grad">Obsidian out.</span></h1>
  <p class="lede">Drop a PDF and get back a ready-to-open vault — clean Markdown plus
     every figure, with the structure that usually gets mangled left intact.</p>

  <div class="card">
    <form id="f">
      <label class="drop" id="drop" for="pdf">
        <div class="ico">📄</div>
        <div class="big" id="droptext">Drop a PDF here, or click to choose</div>
        <div class="small">stays on the server only long enough to convert</div>
        <input id="pdf" name="pdf" type="file" accept="application/pdf,.pdf" required>
      </label>
      <div class="title-row">
        <input id="title" name="title" type="text" placeholder="Note title (optional — defaults to the file name)">
      </div>
      <button id="go" type="submit">Convert to Obsidian vault</button>
      <div class="progress" id="progress"><div class="fill" id="fill"></div></div>
      <div id="status"></div>
    </form>
  </div>

  <div class="feat-h">What it handles</div>
  <div class="grid">
    <div class="feat"><div class="t"><span class="ck">✓</span> Heading hierarchy</div>
      <div class="d">Chapters → sections → subsections, inferred from font size & numbering.</div></div>
    <div class="feat"><div class="t"><span class="ck">✓</span> Inline formatting</div>
      <div class="d"><b>Bold</b>, <i>italic</i>, and <code>code</code> spans preserved exactly.</div></div>
    <div class="feat"><div class="t"><span class="ck">✓</span> Figures &amp; charts</div>
      <div class="d">Tiled images reassembled and embedded as <code>![[wikilinks]]</code>.</div></div>
    <div class="feat"><div class="t"><span class="ck">✓</span> Tables</div>
      <div class="d">Real grids become Markdown tables; diagram false-positives dropped.</div></div>
    <div class="feat"><div class="t"><span class="ck">✓</span> Clean text</div>
      <div class="d">Page numbers & running headers stripped; ligatures &amp; accents repaired.</div></div>
    <div class="feat"><div class="t"><span class="ck">✓</span> Table of contents</div>
      <div class="d">Dotted-leader entries rebuilt as a nested, indented list.</div></div>
  </div>

  <div class="foot">Unzip the result and use <code>Open folder as vault</code> in Obsidian.</div>
</div>

<script>
const f = document.getElementById('f');
const go = document.getElementById('go');
const drop = document.getElementById('drop');
const pdf = document.getElementById('pdf');
const droptext = document.getElementById('droptext');
const titleEl = document.getElementById('title');
const status = document.getElementById('status');

function setFile(file) {
  if (!file) return;
  const dt = new DataTransfer(); dt.items.add(file); pdf.files = dt.files;
  drop.classList.add('has');
  droptext.innerHTML = '<span id="fname">' + file.name + '</span>';
  if (!titleEl.value) titleEl.value = file.name.replace(/\\.pdf$/i, '');
}
pdf.addEventListener('change', () => setFile(pdf.files[0]));
['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => { if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });

const progress = document.getElementById('progress');
const fill = document.getElementById('fill');

function reset() {
  go.disabled = false; go.textContent = 'Convert to Obsidian vault';
}
function fail(msg) { status.className='err'; status.textContent = '⚠ ' + msg; progress.classList.remove('show'); reset(); }

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!pdf.files[0]) { status.className='err'; status.textContent='Choose a PDF first.'; return; }
  go.disabled = true; go.innerHTML = '<span class="spin"></span>Converting…';
  status.className=''; status.innerHTML = '<span class="spin"></span>Starting…';
  progress.classList.add('show'); fill.style.width = '0%';

  let job;
  try {
    const res = await fetch('/convert', { method:'POST', body:new FormData(f) });
    if (!res.ok) return fail(await res.text());
    job = await res.json();
  } catch (err) { return fail(err); }

  const es = new EventSource('/events/' + job.job_id);
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === 'progress') {
      if (typeof d.frac === 'number') fill.style.width = Math.round(d.frac * 100) + '%';
      status.className=''; status.innerHTML = '<span class="spin"></span>' + d.msg;
    } else if (d.type === 'done') {
      es.close(); fill.style.width = '100%';
      const a = document.createElement('a');
      a.href = '/download/' + job.job_id; a.download = job.name + '.zip'; a.click();
      status.className='ok'; status.textContent = '✓ ' + d.summary + ' — downloading ' + d.name + '.zip';
      reset();
    } else if (d.type === 'error') {
      es.close(); fail(d.msg);
    }
  };
  es.onerror = () => { es.close(); fail('connection lost'); };
});
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


def _zip_vault(out_dir: str, safe: str) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(out_dir):
            for name in files:
                full = os.path.join(root, name)
                arc = os.path.join(safe, os.path.relpath(full, out_dir))
                zf.write(full, arc)
    buf.seek(0)
    return buf


def _run_job(job_id: str, pdf_path: str, title: str, safe: str, work: str):
    job = JOBS[job_id]
    q = job["q"]
    try:
        out_dir = os.path.join(work, safe)
        res = convert(pdf_path, out_dir, title, to="obsidian",
                      progress=lambda m, frac=None: q.put({"type": "progress",
                                                           "msg": m, "frac": frac}))
        job["zip"] = _zip_vault(out_dir, safe)
        job["summary"] = (f"{res['pages']} pages · {res['figures']} figures · "
                          f"{res['tables']} tables")
        q.put({"type": "done", "summary": job["summary"], "name": safe})
    except Exception as e:  # surface to the browser
        q.put({"type": "error", "msg": str(e)})
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post("/convert")
def convert_endpoint():
    up = request.files.get("pdf")
    if not up or not up.filename:
        return "No PDF uploaded.", 400

    title = (request.form.get("title") or "").strip() or os.path.splitext(up.filename)[0]
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "vault"

    work = tempfile.mkdtemp(prefix="docsidian_")
    pdf_path = os.path.join(work, "input.pdf")
    up.save(pdf_path)

    job_id = secrets.token_urlsafe(12)
    JOBS[job_id] = {"q": queue.Queue(), "zip": None, "summary": "", "ts": time.time()}
    threading.Thread(target=_run_job, args=(job_id, pdf_path, title, safe, work),
                     daemon=True).start()
    return {"job_id": job_id, "name": safe}


@app.get("/events/<job_id>")
def events(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return "Unknown job.", 404

    def stream():
        q = job["q"]
        while True:
            try:
                evt = q.get(timeout=30)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(evt)}\n\n"
            if evt["type"] in ("done", "error"):
                break

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/download/<job_id>")
def download(job_id: str):
    job = JOBS.get(job_id)
    if not job or not job.get("zip"):
        return "Not ready.", 404
    buf = job["zip"]
    buf.seek(0)
    JOBS.pop(job_id, None)  # one-shot download, then forget
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{job_id}.zip")


if __name__ == "__main__":
    print("Docsidian running at http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
