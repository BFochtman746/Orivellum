"""
Build the Orivellum Installation & Setup Guide DOCX.
Run with: uv run python scripts/build_manual.py
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

os.makedirs("docs/manual", exist_ok=True)
doc = Document()

# ── Page margins ──────────────────────────────────────────────────────────────
for section in doc.sections:
    section.top_margin    = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin   = Inches(1.0)
    section.right_margin  = Inches(1.0)

# ── Helpers ───────────────────────────────────────────────────────────────────

def shade_cell(cell, fill_hex: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tcPr.append(shd)

def shade_para(p, fill_hex: str):
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    pPr.append(shd)

def h(level: int, text: str, color=None):
    p = doc.add_heading(text, level=level)
    if color:
        for run in p.runs:
            run.font.color.rgb = RGBColor(*color)
    return p

def body(text: str):
    p = doc.add_paragraph(text)
    p.paragraph_format.space_after = Pt(6)
    return p

def bullet(text: str):
    p = doc.add_paragraph(text, style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    return p

def numbered(text: str):
    p = doc.add_paragraph(text, style="List Number")
    p.paragraph_format.space_after = Pt(3)
    return p

def code(text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(6)
    p.paragraph_format.left_indent  = Inches(0.3)
    run = p.add_run(text)
    run.font.name  = "Courier New"
    run.font.size  = Pt(9)
    run.font.color.rgb = RGBColor(0x1a, 0x1a, 0x2e)
    shade_para(p, "F0F4F8")
    return p

def note(text: str, fill="FFF8E1", icon="💡", text_rgb=(0x5a, 0x42, 0x00)):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent  = Inches(0.2)
    p.paragraph_format.right_indent = Inches(0.2)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(8)
    run = p.add_run(f"{icon}  {text}")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(*text_rgb)
    shade_para(p, fill)
    return p

def tip(text: str):
    return note(text, "E8F5E9", "✅", (0x1a, 0x5c, 0x2e))

def warn(text: str):
    return note(text, "FDE8E8", "⚠️", (0x8b, 0x1a, 0x1a))

def add_image(path: str, width=6.0, caption=None):
    try:
        doc.add_picture(path, width=Inches(width))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if caption:
            cp = doc.add_paragraph(caption)
            cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cp.paragraph_format.space_after = Pt(12)
            for run in cp.runs:
                run.font.size    = Pt(9)
                run.font.italic  = True
                run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    except Exception as e:
        body(f"[Image: {caption or path}]  ({e})")

def dark_table_header(row, cols, fill="1a3a2a"):
    for cell in row.cells:
        for para in cell.paragraphs:
            for run in para.runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade_cell(cell, fill)

def add_table(headers, rows, col_widths, row_fill=("FFFFFF", "F4F7F5")):
    tbl = doc.add_table(rows=1, cols=len(headers))
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h_text in enumerate(headers):
        tbl.rows[0].cells[i].text = h_text
    dark_table_header(tbl.rows[0], len(headers))
    for idx, data_row in enumerate(rows):
        r = tbl.add_row().cells
        fill = row_fill[idx % 2]
        for j, val in enumerate(data_row):
            r[j].text = str(val)
            shade_cell(r[j], fill)
    for row in tbl.rows:
        for i, cell in enumerate(row.cells):
            if i < len(col_widths):
                cell.width = col_widths[i]
    doc.add_paragraph()
    return tbl

# ─────────────────────────────────────────────────────────────────────────────
# COVER PAGE
# ─────────────────────────────────────────────────────────────────────────────
add_image("docs/manual/hero-banner.jpg", 6.5)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("Orivellum")
r.font.size = Pt(36)
r.font.bold = True
r.font.color.rgb = RGBColor(0x1a, 0x3a, 0x2a)

p2 = doc.add_paragraph()
p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
r2 = p2.add_run("Complete Installation & Setup Guide")
r2.font.size = Pt(18)
r2.font.color.rgb = RGBColor(0x9a, 0x7b, 0x2e)

p3 = doc.add_paragraph()
p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
r3 = p3.add_run("v1.0  ·  August 2026  ·  For: Brian Fochtman")
r3.font.size = Pt(11)
r3.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# TABLE OF CONTENTS
# ─────────────────────────────────────────────────────────────────────────────
h(1, "Table of Contents")
toc = [
    ("1.", "Quick-Start Checklist"),
    ("2.", "Prerequisites"),
    ("3.", "Installation — Mac / Linux"),
    ("4.", "Installation — Windows"),
    ("5.", "Lemonade AI Server Setup"),
    ("6.", "Recommended LLM Models for Lemonade"),
    ("7.", "First Launch & Configuration"),
    ("8.", "Mobile App (Expo Go)"),
    ("9.", "Environment Variables Reference"),
    ("10.", "Troubleshooting"),
]
for num, title in toc:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    nr = p.add_run(f"{num}  ")
    nr.font.bold = True
    nr.font.color.rgb = RGBColor(0x9a, 0x7b, 0x2e)
    p.add_run(title)
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 1. QUICK-START CHECKLIST
# ─────────────────────────────────────────────────────────────────────────────
h(1, "1.  Quick-Start Checklist")
body("Use this checklist before you begin. Check each item off as you complete it.")

add_image("docs/manual/install-steps.jpg", 5.5, "Four-step installation overview")

h(2, "Prerequisites checklist")
for item in [
    "☐  Git 2.35 +",
    "☐  Node.js 18 LTS or 20 LTS",
    "☐  pnpm 8 +  (npm install -g pnpm)",
    "☐  Python 3.12 +",
    "☐  uv  (pip install uv  OR  winget install astral-sh.uv)",
    "☐  8 GB RAM minimum  (16 GB recommended for local LLM)",
    "☐  AMD or NVIDIA GPU  (optional — for Lemonade GPU acceleration)",
    "☐  Expo Go app on your phone  (optional — for mobile access)",
]:
    bullet(item)

h(2, "Installation checklist")
for item in [
    "☐  Clone the repository",
    "☐  Run  pnpm install",
    "☐  Run  uv sync",
    "☐  Copy and edit .env  (optional)",
    "☐  Start with  bash scripts/dev.sh",
    "☐  (Optional) Install Lemonade and load a model",
    "☐  (Optional) Scan the QR code with Expo Go",
]:
    bullet(item)
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 2. PREREQUISITES
# ─────────────────────────────────────────────────────────────────────────────
h(1, "2.  Prerequisites")

h(2, "Node.js")
body("Download the LTS release from https://nodejs.org. Run the installer and verify with:")
code("node --version   # must be 18.x or 20.x")
tip("Use Node 20 LTS for best compatibility. Versions older than 18 are not supported.")

h(2, "pnpm")
body("Orivellum uses pnpm workspaces. Install it globally after Node.js:")
code("npm install -g pnpm\npnpm --version   # should be 8 or higher")

h(2, "Python 3.12 + and uv")
body("Python 3.12 or newer is required for the API server. uv is the recommended installer:")
code("# Mac / Linux\ncurl -LsSf https://astral.sh/uv/install.sh | sh\n\n# Windows (PowerShell)\npowershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"")
tip("uv is much faster than pip. It creates a virtual environment and pins all dependencies automatically via uv.lock.")

h(2, "Git")
body("Install from https://git-scm.com or via your package manager:")
code("# Mac\nbrew install git\n\n# Ubuntu / Debian\nsudo apt install git\n\n# Windows — included with Git for Windows installer")
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 3. INSTALLATION — MAC / LINUX
# ─────────────────────────────────────────────────────────────────────────────
h(1, "3.  Installation — Mac / Linux")

h(2, "Step 1 — Clone the repository")
code("git clone https://github.com/BFo/orivellum-main.git\ncd orivellum-main")

h(2, "Step 2 — Install JavaScript / TypeScript dependencies")
code("pnpm install")
body("Installs all workspace packages (web UI, mobile app, shared libraries, API client). "
     "Expect 2–5 minutes on first run.")

h(2, "Step 3 — Set up the Python virtual environment")
code("uv sync")
body("Creates .venv and installs all Python packages from uv.lock. "
     "Subsequent runs complete in seconds.")

h(2, "Step 4 — Create your .env file  (optional)")
body("Copy the example and fill in any secrets you need:")
code("cp artifacts/api-server/.env.example artifacts/api-server/.env\n# Edit with nano, vim, or VS Code")
tip("The app works without any .env file. "
    "AI model URLs and keys can also be set through the web Settings page.")

h(2, "Step 5 — Start the application")
code("# API server + Web UI\nbash scripts/dev.sh\n\n# API server + Web UI + Expo mobile\nbash scripts/dev.sh --mobile")
body("The script starts FastAPI on port 8080, waits for a healthy response, then starts Vite on port 5173.")
body("Open your browser to:")
code("http://localhost:5173")
warn("Keep this terminal open. Press Ctrl+C to stop all services at once.")
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 4. INSTALLATION — WINDOWS
# ─────────────────────────────────────────────────────────────────────────────
h(1, "4.  Installation — Windows")

h(2, "Step 1 — Install prerequisites via winget")
body("Open PowerShell as Administrator and run:")
code("winget install OpenJS.NodeJS.LTS\n"
     "winget install pnpm.pnpm\n"
     "winget install astral-sh.uv\n"
     "winget install Git.Git")
body("Restart your terminal after installation so the new tools appear in PATH.")

h(2, "Step 2 — Clone and install")
code("git clone https://github.com/BFo/orivellum-main.git\n"
     "cd orivellum-main\n"
     "pnpm install\n"
     "uv sync")

h(2, "Step 3 — Development mode  (Vite dev server + API)")
body("In a PowerShell or Git Bash window:")
code("# Git Bash\nbash scripts/dev.sh\n\n# PowerShell — set port then call bash\n$env:API_PORT='8080'; bash scripts/dev.sh")
tip("Git for Windows includes Git Bash. If bash is not found, install Git for Windows from https://git-scm.com/download/win")

h(2, "Step 4 — Production / Appliance mode  (no dev server)")
body("Builds the UI bundle once, then serves everything from a single FastAPI process:")
code(".\\scripts\\start.ps1\n\n# Skip rebuilding the UI (fast restart):\n.\\scripts\\start.ps1 -SkipBuild\n\n# With Expo mobile server:\n.\\scripts\\start.ps1 -Mobile")
body("Then open:")
code("http://localhost:8080/orivellum-ui/")
tip("Bookmark this URL and use Add to Home Screen in your browser for a near-native app experience.")

h(2, "Step 5 — Auto-start at Windows login  (optional)")
body("Register Orivellum as a Task Scheduler task so it starts automatically:")
code(".\\scripts\\windows\\register-boot.ps1")
note("After registration Orivellum starts at login. "
     "Run register-boot.ps1 -Unregister to remove it.")
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 5. LEMONADE AI SERVER SETUP
# ─────────────────────────────────────────────────────────────────────────────
h(1, "5.  Lemonade AI Server Setup")
body("Lemonade is a local LLM inference server compatible with the OpenAI API. "
     "It runs AI models on your own GPU (AMD ROCm or NVIDIA CUDA) or CPU, "
     "keeping every conversation and document 100% private and offline.")

add_image("docs/manual/lemonade-setup.jpg", 5.5,
          "Lemonade loads GGUF model files and serves them via OpenAI-compatible API")

h(2, "Install Lemonade")
body("Download and install from the official releases:")
code("# AMD GPU (ROCm) or CPU\npip install lemonade-server\n\n"
     "# NVIDIA GPU (CUDA)\npip install lemonade-server[cuda]\n\n"
     "# Windows installer available at:\n"
     "# https://github.com/lemonade-llm/lemonade/releases")

h(2, "Start the Lemonade server")
code("# Default port 13305\nlemonade serve\n\n"
     "# Load a specific model on start\n"
     "lemonade serve --model /path/to/model.gguf\n\n"
     "# Custom port\nlemonade serve --port 13305")
tip("Lemonade defaults to port 13305, which matches the Orivellum default setting.")

h(2, "Connect Orivellum to Lemonade")
numbered("Open Orivellum in your browser")
numbered("Go to System page → Settings section")
numbered('Under "Local AI Model (Lemonade)" set the URL to:')
code("http://127.0.0.1:13305/api/v1")
numbered("Optionally enter a Model ID from the table in Section 6")
numbered("Click Save Settings — the green indicator confirms the connection")

h(2, "GPU vs CPU mode")
body("Lemonade automatically detects available hardware:")
bullet("AMD GPU (ROCm): fastest — requires ROCm 5.7+")
bullet("NVIDIA GPU (CUDA): fast — requires CUDA 12+")
bullet("CPU: works on any machine; 5–15x slower than GPU")
warn("On CPU, use models with 4 billion parameters or fewer "
     "(Phi-4 Mini, Gemma 2 2B, Llama 3.2 3B). Larger models will be too slow for interactive use.")
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 6. RECOMMENDED LLM MODELS
# ─────────────────────────────────────────────────────────────────────────────
h(1, "6.  Recommended LLM Models for Lemonade")
body("All models below are freely available in GGUF format from Hugging Face. "
     "They are OpenAI-API-compatible and work directly with Lemonade. "
     "Download them and load via the Lemonade CLI or web interface.")
body("Quantization guide:  Q4_K_M = smaller/faster, some quality loss  •  Q8_0 = larger/slower, near full quality")

models_headers = ["Model", "Parameters", "Quant", "Best For", "VRAM / RAM"]
models_rows = [
    ("Meta Llama 3.2 3B Instruct",    "3B",   "Q8_0",   "Fast chat, summaries, CPU",     "3–4 GB"),
    ("Meta Llama 3.1 8B Instruct",    "8B",   "Q4_K_M", "General purpose, documents",    "5–6 GB"),
    ("Meta Llama 3.1 8B Instruct",    "8B",   "Q8_0",   "General purpose — high quality","9–10 GB"),
    ("Mistral 7B Instruct v0.3",      "7B",   "Q4_K_M", "General chat, good baseline",   "5–6 GB"),
    ("Microsoft Phi-4 Mini Instruct", "3.8B", "Q4_K_M", "CPU-friendly, reasoning tasks", "3–4 GB"),
    ("Microsoft Phi-4 Mini Instruct", "3.8B", "Q8_0",   "CPU-friendly — high quality",   "5–6 GB"),
    ("Qwen 2.5 7B Instruct",          "7B",   "Q4_K_M", "Multilingual, code, analysis",  "5–6 GB"),
    ("Qwen 2.5 7B Instruct",          "7B",   "Q8_0",   "Multilingual — high quality",   "8–9 GB"),
    ("Google Gemma 2 9B IT",          "9B",   "Q4_K_M", "Long context, deep analysis",   "6–7 GB"),
    ("Google Gemma 2 2B IT",          "2B",   "Q8_0",   "Ultra-fast, any hardware",      "2–3 GB"),
    ("Mistral NeMo 12B Instruct",     "12B",  "Q4_K_M", "Extended context (128k)",       "8–9 GB"),
    ("Phi-3.5 Mini Instruct",         "3.8B", "Q4_K_M", "Lightweight, low latency",      "3–4 GB"),
]
add_table(
    models_headers, models_rows,
    [Inches(1.85), Inches(0.7), Inches(0.75), Inches(1.65), Inches(0.9)],
)
tip("Start with Phi-4 Mini Q4_K_M if you are on CPU. "
    "For 8 GB VRAM, Llama 3.1 8B Q4_K_M offers the best quality-to-speed ratio. "
    "For 16 GB+ VRAM, Mistral NeMo 12B gives excellent context handling.")
note("Download GGUF models from: https://huggingface.co/models?sort=downloads&search=GGUF  "
     "Search the model name above and look for files ending in -Q4_K_M.gguf or -Q8_0.gguf.")

h(2, "Loading a model in Lemonade")
code("# Load a GGUF model file\nlemonade load /path/to/Llama-3.1-8B-Instruct-Q4_K_M.gguf\n\n"
     "# List all loaded models\nlemonade list\n\n"
     "# Example output:\n"
     "# Llama-3.1-8B-Instruct-Q4_K_M   (loaded)  <-- use this name in Orivellum Settings")

h(2, "Setting the model in Orivellum")
body('Copy the model name shown by "lemonade list" and paste it into the Model ID field in Orivellum Settings. '
     'Leave the field blank to let Lemonade use whichever model is currently loaded.')
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 7. FIRST LAUNCH & CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
h(1, "7.  First Launch & Configuration")

h(2, "What you see on first launch")
numbered("The web UI opens at  http://localhost:5173")
numbered("Dashboard shows a time-of-day greeting and empty sections")
numbered("Library (sidebar → Import) is where you upload your first documents")
numbered("Chat works immediately — try asking anything even without a local model")

h(2, "Connect a local AI model")
numbered("Start Lemonade and load a model  (see Section 5)")
numbered("Open Orivellum → System page → Settings card")
numbered('Under "Local AI (Lemonade)" enter:  http://127.0.0.1:13305/api/v1')
numbered("Click Save — the status indicator turns green")

h(2, "Import your first document")
numbered("Click Import in the sidebar")
numbered("Drag and drop a PDF, DOCX, EPUB, or image — or click to browse")
numbered("Orivellum extracts knowledge, auto-assigns it to a Work, and makes it searchable in Chat")
tip("Works are like projects or books — they group related documents. "
    "Orivellum automatically suggests the right Work when you import.")

h(2, "Start your first chat")
body("Click Chat in the sidebar. "
     "Scope a conversation to a specific Work using the Work selector at the top — "
     "the AI will draw exclusively from that Work's documents for precise, cited answers.")
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 8. MOBILE APP (EXPO GO)
# ─────────────────────────────────────────────────────────────────────────────
h(1, "8.  Mobile App (Expo Go)")

add_image("docs/manual/mobile-preview.jpg", 3.5, "Orivellum mobile — chat and library views")

h(2, "Requirements")
bullet("iPhone (iOS 16+) or Android (10+)")
bullet("Expo Go installed from App Store or Play Store")
bullet("Same WiFi network as the computer running Orivellum")

h(2, "Start with mobile support")
code("# Mac / Linux\nbash scripts/dev.sh --mobile\n\n"
     "# Windows PowerShell\n.\\scripts\\start.ps1 -Mobile")
body("Expo prints a QR code in the terminal. "
     "Scan it with your phone camera (iOS) or the Expo Go app (Android).")

h(2, "Configure the API address")
body("The mobile app reads the server address from EXPO_PUBLIC_DOMAIN. Set it in the mobile .env file:")
code("# artifacts/mobile/.env\nEXPO_PUBLIC_DOMAIN=192.168.1.100:8080\n\n"
     "# Replace 192.168.1.100 with your computer LAN IP")
tip("Find your LAN IP:  ipconfig (Windows)  or  ifconfig | grep 192.168 (Mac/Linux)")

h(2, "Features on mobile")
for feat in [
    "Chat with all your Works and documents — full AI context",
    "Upload files from your photo library or the Files app",
    "Listen to generated audiobooks with in-app player",
    "Browse and manage your knowledge library",
    "Mail steward — view and action AI-assessed Outlook emails",
    "Works, Books, Learn, Projects, and Write desk",
]:
    bullet(feat)
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 9. ENVIRONMENT VARIABLES REFERENCE
# ─────────────────────────────────────────────────────────────────────────────
h(1, "9.  Environment Variables Reference")
body("Create  artifacts/api-server/.env  (copy from .env.example). All variables are optional — sensible defaults apply.")

env_headers = ["Variable", "Default", "Description"]
env_rows = [
    ("API_PORT",              "8080",                          "Port for the FastAPI server"),
    ("SESSION_SECRET",        "(auto-generated)",              "Cookie session secret — set a strong random string in production"),
    ("LEMONADE_URL",          "http://127.0.0.1:13305/api/v1","Lemonade API base URL"),
    ("LEMONADE_MODEL",        "(blank = server default)",      "Model ID to request from Lemonade"),
    ("AI_EXTRACTION_ENABLED", "false",                         "Enable LLM-powered knowledge extraction on import"),
    ("TAVILY_API_KEY",        "(blank)",                       "Tavily search key for online research feature"),
    ("EXPO_PUBLIC_DOMAIN",    "localhost:8080",                "API host:port used by the mobile app"),
    ("DATA_DIR",              "./data",                        "Directory where Orivellum stores its database and files"),
    ("DEBUG",                 "0",                             "Set to 1 for verbose API logging"),
]
add_table(env_headers, env_rows, [Inches(1.9), Inches(1.6), Inches(2.5)])
doc.add_page_break()

# ─────────────────────────────────────────────────────────────────────────────
# 10. TROUBLESHOOTING
# ─────────────────────────────────────────────────────────────────────────────
h(1, "10.  Troubleshooting")

h(2, "API server does not start")
bullet("Check Python version:  python --version  — must be 3.12 or newer")
bullet("Re-run  uv sync  to ensure all packages are installed")
bullet("Check port 8080 is free:  lsof -i :8080  (Mac/Linux)  or  netstat -ano | findstr :8080  (Windows)")
code("# Start with verbose output to see the error\nDEBUG=1 uv run python -m orivellum.api.main")

h(2, "Web UI shows a blank screen")
bullet("Ensure the API server started — dev.sh waits for it but check for crash messages")
bullet("Open browser console (F12) and look for red error messages")
bullet("Hard-reload:  Ctrl+Shift+R  (Windows/Linux)  or  Cmd+Shift+R  (Mac)")

h(2, "Lemonade connection fails in Settings")
bullet("Verify Lemonade is running:  curl http://127.0.0.1:13305/api/v1/models")
bullet("Check the URL exactly matches the Settings field — no trailing slash")
bullet("Ensure a model is loaded before sending messages")
code("# Test Lemonade from the terminal\ncurl http://127.0.0.1:13305/api/v1/models\n\n"
     "# Expected response:\n# {\"object\":\"list\",\"data\":[{\"id\":\"...\"}]}")

h(2, "Mobile app cannot connect to server")
bullet("Both phone and computer must be on the same WiFi network")
bullet("Set EXPO_PUBLIC_DOMAIN to your computer LAN IP (not localhost)")
bullet("Windows: allow inbound connections on port 8080 in Windows Firewall")
code("# Windows PowerShell — open firewall port\nNew-NetFirewallRule -DisplayName 'Orivellum' "
     "-Direction Inbound -Port 8080 -Protocol TCP -Action Allow")

h(2, "Slow AI responses with local model")
bullet("Switch to a smaller quantization: Q4_K_M instead of Q8_0")
bullet("Use a smaller model: Phi-4 Mini 3.8B instead of Llama 3.1 8B")
bullet("Ensure Lemonade is using the GPU, not CPU:  lemonade status")
tip("Q4_K_M at 8B offers 90% of Q8_0 quality at about 55% of the file size and memory usage.")

h(2, "pnpm install fails on Windows")
bullet("Run PowerShell as Administrator")
bullet("Set execution policy:  Set-ExecutionPolicy RemoteSigned")
bullet("Then re-run:  pnpm install")

# ─────────────────────────────────────────────────────────────────────────────
# Save
# ─────────────────────────────────────────────────────────────────────────────
out = "docs/manual/Orivellum_Installation_Guide.docx"
doc.save(out)
print(f"Saved: {out}")
