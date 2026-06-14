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
        <div class="big" id="droptext">Drop PDFs here, or click to choose</div>
        <div class="small">one or many — you get two drop-in folders (notes + attachments) for your existing vault</div>
        <input id="pdf" name="pdf" type="file" accept="application/pdf,.pdf" multiple required>
      </label>
      <div class="title-row">
        <input id="title" name="title" type="text" placeholder="Note title (optional, single file only — defaults to the file name)">
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

  <div class="foot">Unzip and drag both folders — the notes folder and <code>attachments/</code> — into your existing vault.</div>
</div>

<script>
const f = document.getElementById('f');
const go = document.getElementById('go');
const drop = document.getElementById('drop');
const pdf = document.getElementById('pdf');
const droptext = document.getElementById('droptext');
const titleEl = document.getElementById('title');
const status = document.getElementById('status');

function showFiles(list) {
  if (!list || !list.length) return;
  pdf.files = list;
  drop.classList.add('has');
  if (list.length === 1) {
    droptext.innerHTML = '<span id="fname">' + list[0].name + '</span>';
    if (!titleEl.value) titleEl.value = list[0].name.replace(/\\.pdf$/i, '');
  } else {
    droptext.innerHTML = '<span id="fname">' + list.length + ' PDFs selected</span>';
  }
}
pdf.addEventListener('change', () => showFiles(pdf.files));
['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => { if (e.dataTransfer.files.length) showFiles(e.dataTransfer.files); });

const progress = document.getElementById('progress');
const fill = document.getElementById('fill');
let timer = null;

function reset() { go.disabled = false; go.textContent = 'Convert to Obsidian vault'; }
function fail(msg) {
  if (timer) clearInterval(timer);
  status.className='err'; status.textContent = '⚠ ' + (msg || 'something went wrong');
  progress.classList.remove('show'); reset();
}

// One plain request — no SSE. The bar is a time-based estimate that eases toward
// ~92% and snaps to 100% when the zip arrives. Reliable behind any proxy/cold start.
function startBar() {
  progress.classList.add('show'); fill.style.width = '0%';
  let pct = 0, t = 0;
  const stages = [
    [0,  'Uploading PDF…'],
    [1,  'Extracting text, figures & tables…'],
    [6,  'Rendering Markdown…'],
    [12, 'Almost there — assembling the vault…'],
    [22, 'Still working — large file or the server is waking up…'],
  ];
  timer = setInterval(() => {
    t += 0.2;
    pct = Math.min(92, pct + (92 - pct) * 0.04);   // ease toward 92%
    fill.style.width = pct.toFixed(1) + '%';
    let label = stages[0][1];
    for (const [at, txt] of stages) if (t >= at) label = txt;
    status.className=''; status.innerHTML = '<span class="spin"></span>' + label;
  }, 200);
}

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!pdf.files[0]) { status.className='err'; status.textContent='Choose a PDF first.'; return; }
  go.disabled = true; go.innerHTML = '<span class="spin"></span>Converting…';
  startBar();
  try {
    const res = await fetch('/convert', { method:'POST', body:new FormData(f) });
    if (!res.ok) return fail(await res.text());
    const blob = await res.blob();
    clearInterval(timer); fill.style.width = '100%';
    const name = (res.headers.get('X-Vault-Name') || 'vault') + '.zip';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href=url; a.download=name; a.click();
    URL.revokeObjectURL(url);
    status.className='ok';
    status.textContent = '✓ ' + (res.headers.get('X-Summary') || 'Done') + ' — downloaded ' + name;
    reset();
  } catch (err) { fail(err); }
});
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_").strip() or "vault"


@app.post("/convert")
def convert_endpoint():
    """Convert one or more PDFs into a single mergeable Obsidian vault and
    stream it back as a zip with two drop-in folders: a notes folder with the
    page(s) and an attachments/ folder with every figure. Synchronous on
    purpose — reliable behind any proxy.

    Move both folders into an existing vault: notes are added as pages and
    attachments merge (figure filenames are namespaced per document, so many
    papers can live in one vault without colliding).
    """
    files = [f for f in request.files.getlist("pdf") if f and f.filename]
    if not files:
        return "No PDF uploaded.", 400
    title_field = (request.form.get("title") or "").strip()

    work = tempfile.mkdtemp(prefix="docsidian_")
    out_dir = os.path.join(work, "vault")  # one shared vault for all uploads
    try:
        pages = figures = tables = 0
        for idx, up in enumerate(files):
            # Only honour a custom title when converting a single file.
            title = (title_field if (title_field and len(files) == 1)
                     else os.path.splitext(up.filename)[0])
            pdf_path = os.path.join(work, f"in{idx}.pdf")
            up.save(pdf_path)
            res = convert(pdf_path, out_dir, title, to="obsidian")
            pages += res["pages"]; figures += res["figures"]; tables += res["tables"]

        n = len(files)
        notes_folder = (_safe_name(title_field) if (title_field and n == 1)
                        else (_safe_name(os.path.splitext(files[0].filename)[0])
                              if n == 1 else "Docsidian Notes"))

        # Zip as two clean, drop-in folders so nothing has to be hand-sorted:
        #   <notes_folder>/  -> the markdown page(s)
        #   attachments/     -> every figure (filenames namespaced per document)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, names in os.walk(out_dir):
                for name in names:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, out_dir)
                    if rel.split(os.sep)[0] == "attachments":
                        arc = rel                      # keep attachments/ at top level
                    elif name.endswith(".md"):
                        arc = os.path.join(notes_folder, name)  # notes into their folder
                    else:
                        arc = rel
                    zf.write(full, arc)
        buf.seek(0)

        vault_name = notes_folder if n == 1 else "docsidian-vault"
        resp = send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"{vault_name}.zip")
        resp.headers["X-Summary"] = (f"{n} doc{'s' if n > 1 else ''} · {pages} pages · "
                                     f"{figures} figures · {tables} tables")
        resp.headers["X-Vault-Name"] = vault_name
        return resp
    except Exception as e:
        return str(e), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    print("Docsidian running at http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
