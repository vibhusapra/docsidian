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

  #status { margin-top:16px; font-size:14px; min-height:20px; }
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

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!pdf.files[0]) { status.className='err'; status.textContent='Choose a PDF first.'; return; }
  go.disabled = true; go.innerHTML = '<span class="spin"></span>Converting…';
  status.className=''; status.innerHTML = '<span class="spin"></span>Extracting text, figures & tables…';
  try {
    const res = await fetch('/convert', { method:'POST', body:new FormData(f) });
    if (!res.ok) { status.className='err'; status.textContent='⚠ ' + (await res.text()); return; }
    const blob = await res.blob();
    const name = (res.headers.get('X-Vault-Name') || 'vault') + '.zip';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href=url; a.download=name; a.click();
    URL.revokeObjectURL(url);
    status.className='ok';
    status.textContent = '✓ ' + (res.headers.get('X-Summary') || 'Done') + ' — downloaded ' + name;
  } catch (err) {
    status.className='err'; status.textContent = '⚠ ' + err;
  } finally {
    go.disabled = false; go.textContent = 'Convert to Obsidian vault';
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
