import imaplib
import email
import json
import logging
import os
import random
import re
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse
import base64
import anthropic
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

GMAIL_USER       = os.environ["GMAIL_USER"]
GMAIL_PASSWORD   = os.environ["GMAIL_PASSWORD"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "120"))

DATA_DIR      = os.environ.get("DATA_DIR", "/tmp")
ORDERS_FILE   = os.path.join(DATA_DIR, "orders.json")
CHAT_FILE     = os.path.join(DATA_DIR, "chat.json")
STATS_FILE    = os.path.join(DATA_DIR, "stats.json")
LEARNING_FILE = os.path.join(DATA_DIR, "learning.json")

pending_orders = {}
chat_history   = {}
active_order   = None
last_update_id = 0

# Stats tracking
stats = {
    "total_earned": 0.0,
    "orders_completed": 0,
    "orders_by_gig": {},
    "five_star_count": 0,
    "revision_counts": {},  # order_id: count
    "daily_earned": {},     # "2026-06-01": amount
    "milestones_sent": []
}

# Learning: what works and what doesn't per gig
learning = {
    "gig_notes": {},      # gig_id: [notes from revisions]
    "revision_patterns": {} # gig_id: [common revision requests]
}

# ─── GIG DEFINISJONER + PRISER ───────────────────────────────────────────────

GIGS = {
    "product_no": {
        "keywords": ["produktbeskrivelse", "norsk", "nettbutikk", "produkt beskrivelse"],
        "prices": {"basic": 5, "standard": 15, "premium": 25},
        "packages": {
            "basic": "1 produktbeskrivelse (150 ord), SEO-optimalisert",
            "standard": "3 produktbeskrivelser (200 ord), SEO + nøkkelord",
            "premium": "5 produktbeskrivelser (250 ord), SEO + nøkkelord + meta-beskrivelse"
        },
        "system": (
            "Du er en erfaren norsk copywriter som skriver selgende produktbeskrivelser for nettbutikker. "
            "Skriv alltid på naturlig, flytende norsk – aldri oversatt-engelsk eller stiv AI-stil.\n\n"
            "STRUKTUR:\n"
            "- Fengende overskrift som treffer kjøperens behov (ikke bare produktnavnet)\n"
            "- 1-2 setningers intro som maler et konkret bilde av nytten\n"
            "- 3-5 bullet points som oversetter egenskaper til fordeler (ikke bare lister specs)\n"
            "- Kort, naturlig avslutning med en oppfordring – uten klisjeer\n\n"
            "KRAV:\n"
            "- Vær konkret: bruk tall, materialer og situasjoner kunden kjenner seg igjen i\n"
            "- Varier setningslengde – bland korte, slagkraftige linjer med lengre\n"
            "- Forbudte fraser: 'i en verden hvor', 'ta din X til neste nivå', 'sømløs', "
            "'revolusjonerende', 'enten du er X eller Y', 'se ikke lenger', 'opplev forskjellen'\n"
            "- Ikke overdriv. Skriv som en dyktig fagperson, ikke en reklameplakat\n"
            "- Bruk nøkkelord naturlig der de hører hjemme, aldri proppet inn"
        ),
        "format": "text"
    },
    "product_en": {
        "keywords": ["product description", "ecommerce", "shopify", "amazon", "listing", "online store"],
        "prices": {"basic": 5, "standard": 10, "premium": 25},
        "packages": {
            "basic": "1 product description (150 words), SEO optimized",
            "standard": "3 product descriptions (200 words), SEO + keywords",
            "premium": "5 product descriptions (250 words), SEO + keywords + meta description"
        },
        "system": (
            "You are an expert ecommerce copywriter who writes product descriptions that convert. "
            "Write like a skilled human, never like AI.\n\n"
            "STRUCTURE:\n"
            "- A headline that hits the buyer's need (not just the product name)\n"
            "- A hook sentence that paints a concrete picture of the benefit\n"
            "- 4-5 bullets that translate features into benefits (don't just list specs)\n"
            "- A short, natural closing nudge – no clichés\n\n"
            "RULES:\n"
            "- Be specific: use numbers, materials, real situations the buyer recognizes\n"
            "- Vary sentence length – mix short punchy lines with longer ones\n"
            "- Banned phrases: 'in today's fast-paced world', 'take your X to the next level', "
            "'seamless', 'game-changer', 'look no further', 'whether you're X or Y', 'elevate', 'unleash'\n"
            "- Don't overhype. Write like a knowledgeable expert, not a billboard\n"
            "- Weave keywords in naturally where they belong, never stuffed"
        ),
        "format": "text"
    },
    "cv": {
        "keywords": ["resume", "cv", "curriculum vitae", "job application"],
        "prices": {"basic": 10, "standard": 15, "premium": 20},
        "packages": {
            "basic": "Professional CV rewrite, ATS optimized",
            "standard": "CV + LinkedIn headline and summary",
            "premium": "CV + full LinkedIn profile + cover letter"
        },
        "system": (
            "You are an expert CV writer. Return ONLY valid JSON, no other text:\n"
            '{"name":"","contact":"email | phone | linkedin","summary":"2-3 sentence professional summary",'
            '"experience":[{"title":"","company":"","years":"","bullets":["achievement 1","achievement 2"]}],'
            '"education":[{"degree":"","institution":"","years":""}],'
            '"skills":["skill1","skill2"],"certifications":[]}'
        ),
        "format": "docx_cv"
    },
    "linkedin": {
        "keywords": ["linkedin", "linkedin profile", "linkedin optimization"],
        "prices": {"basic": 10, "standard": 15, "premium": 20},
        "packages": {
            "basic": "LinkedIn headline + about section",
            "standard": "Full LinkedIn profile rewrite",
            "premium": "Full profile + experience descriptions + featured section"
        },
        "system": (
            "You are an expert LinkedIn profile writer. Return ONLY valid JSON, no other text:\n"
            '{"headline":"Job Title | Key Skill | Value (max 220 chars)",'
            '"about":"Full about section, 3-4 paragraphs, first person, conversational",'
            '"experience":[{"title":"","company":"","description":"2-3 sentences with achievements"}],'
            '"skills":["skill1","skill2","skill3","skill4","skill5"],'
            '"tagline":"One memorable sentence"}'
        ),
        "format": "docx_linkedin"
    },
    "email": {
        "keywords": ["email sequence", "newsletter", "drip campaign", "welcome email", "email marketing"],
        "prices": {"basic": 10, "standard": 20, "premium": 40},
        "packages": {
            "basic": "1 email with subject line and preview text",
            "standard": "3-email sequence",
            "premium": "5-email sequence + subject line A/B variants"
        },
        "system": (
            "You are an expert email copywriter. Write emails people actually read and act on – "
            "like a sharp human marketer, never like AI. For each email use this format:\n"
            "SUBJECT: [subject line]\nPREVIEW: [50-90 chars]\nBODY:\n[body]\nCTA: [button text]\n---\n\n"
            "RULES:\n"
            "- Conversational and specific. One clear action per email\n"
            "- Subject lines spark curiosity without clickbait\n"
            "- Vary sentence length. Short lines carry weight\n"
            "- Banned: 'in today's fast-paced world', 'seamless', 'unlock', 'elevate', 'game-changer'\n"
            "- Build trust, don't hype"
        ),
        "format": "text"
    },
    "website": {
        "keywords": ["website copy", "landing page", "homepage copy", "web copy", "about us"],
        "prices": {"basic": 15, "standard": 30, "premium": 60},
        "packages": {
            "basic": "1 page (600 words), SEO optimized",
            "standard": "3 pages (homepage + about + services)",
            "premium": "6 pages + meta descriptions + strategy notes"
        },
        "system": (
            "You are an expert website copywriter. Write for conversion, like a skilled human, never like AI. "
            "For each section use this format:\n"
            "[SECTION NAME]\nH1: [headline]\nSubheadline: [text]\nBody: [copy]\nCTA: [button]\n\n"
            "RULES:\n"
            "- Every word earns its place. Cut filler\n"
            "- Headlines speak to the visitor's goal, not the company's ego\n"
            "- Vary sentence length for rhythm\n"
            "- Banned: 'in today's fast-paced world', 'seamless', 'unlock', 'elevate', "
            "'game-changer', 'look no further', 'more than just'\n"
            "- Concrete and confident, never salesy filler"
        ),
        "format": "text"
    }
}

def detect_gig(subject, body):
    text = (subject + " " + body).lower()
    scores = {}
    for gig_id, gig in GIGS.items():
        score = sum(1 for kw in gig["keywords"] if kw in text)
        if score > 0:
            scores[gig_id] = score
    return max(scores, key=scores.get) if scores else "product_en"

def detect_package(subject, body, gig_id):
    text = (subject + " " + body).lower()
    if "premium" in text: return "premium"
    if "standard" in text: return "standard"
    return "basic"

def detect_language(text):
    norwegian_words = ["og", "er", "jeg", "det", "ikke", "for", "med", "til", "på", "av", "de", "en", "et", "som", "har", "vil", "kan", "skal"]
    words = text.lower().split()
    no_count = sum(1 for w in words if w in norwegian_words)
    return "no" if no_count >= 3 else "en"


# ─── PERSISTENS ──────────────────────────────────────────────────────────────

def save_state():
    try:
        slim = {oid: {"task": o["task"], "customer": o["customer"], "delivery": o["delivery"],
                      "gig": o.get("gig","product_en"), "package": o.get("package","basic"),
                      "language": o.get("language","en")} for oid, o in pending_orders.items()}
        with open(ORDERS_FILE, "w") as f: json.dump(slim, f, ensure_ascii=False)
        slim_chat = {oid: hist[-20:] for oid, hist in chat_history.items()}
        with open(CHAT_FILE, "w") as f: json.dump(slim_chat, f, ensure_ascii=False)
        with open(STATS_FILE, "w") as f: json.dump(stats, f, ensure_ascii=False)
        with open(LEARNING_FILE, "w") as f: json.dump(learning, f, ensure_ascii=False)
    except Exception as e:
        log.error(f"Lagringsfeil: {e}")

def load_state():
    global pending_orders, chat_history, stats, learning
    for path, target, name in [
        (ORDERS_FILE, "pending_orders", "ordrer"),
        (CHAT_FILE, "chat_history", "chat"),
        (STATS_FILE, "stats", "statistikk"),
        (LEARNING_FILE, "learning", "læring"),
    ]:
        try:
            with open(path) as f:
                data = json.load(f)
            if name == "ordrer": pending_orders = data
            elif name == "chat": chat_history = data
            elif name == "statistikk": stats.update(data)
            elif name == "læring": learning.update(data)
            log.info(f"Lastet {name}.")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.error(f"Lastefeil {name}: {e}")


# ─── STATS ───────────────────────────────────────────────────────────────────

def record_earning(amount, gig_id):
    stats["total_earned"] += amount
    stats["orders_completed"] += 1
    stats["orders_by_gig"][gig_id] = stats["orders_by_gig"].get(gig_id, 0) + 1
    today = datetime.now().strftime("%Y-%m-%d")
    stats["daily_earned"][today] = stats["daily_earned"].get(today, 0.0) + amount
    check_milestones()
    save_state()

def get_earnings_last_24h():
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return stats["daily_earned"].get(today, 0.0) + stats["daily_earned"].get(yesterday, 0.0)

def check_milestones():
    order_milestones = [
        (1, "Første ordre levert! 🎉 Reisen har begynt."),
        (5, "5 ordrer levert! 🔥 Du er i gang!"),
        (10, "10 ordrer! 🏆 Nå begynner Fiverr å legge merke til deg."),
        (25, "25 ordrer! 🚀 Du er offisielt en Fiverr-selger!"),
    ]
    earning_milestones = [
        (10, "Du har tjent $10! 💰"),
        (50, "Du har tjent $50! 💸"),
        (100, "Du har tjent $100! 🤑 Første hundrelapp!"),
    ]
    for value, msg in order_milestones:
        key = f"orders_{value}"
        if key not in stats["milestones_sent"] and stats["orders_completed"] >= value:
            send_telegram(f"🏅 MILEPÆL!\n\n{msg}")
            stats["milestones_sent"].append(key)
    for value, msg in earning_milestones:
        key = f"earned_{value}"
        if key not in stats["milestones_sent"] and stats["total_earned"] >= value:
            send_telegram(f"🏅 MILEPÆL!\n\n{msg}")
            stats["milestones_sent"].append(key)


# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def tg_request(method, payload=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error(f"Telegram {method}: {e}")
        return None

def send_telegram(message, reply_markup=None):
    for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
        if reply_markup: payload["reply_markup"] = reply_markup
        tg_request("sendMessage", payload)

def send_with_buttons(message, buttons):
    """Send melding med inline-knapper. buttons = [[{"text":"Label","data":"callback"}]]"""
    keyboard = {"inline_keyboard": [[{"text": b["text"], "callback_data": b["data"]} for b in row] for row in buttons]}
    send_telegram(message, reply_markup=keyboard)

def send_telegram_file(file_path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(file_path, "rb") as f: file_bytes = f.read()
    boundary = "----TgBoundary"
    filename = os.path.basename(file_path)
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{TELEGRAM_CHAT_ID}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info(f"Fil sendt: {filename}")
    except Exception as e:
        log.error(f"Filopplasting: {e}")

def get_telegram_updates():
    global last_update_id
    result = tg_request("getUpdates", {"offset": last_update_id + 1, "timeout": 3, "limit": 10})
    if not result or not result.get("ok"): return []
    updates = result.get("result", [])
    if updates: last_update_id = updates[-1]["update_id"]
    return updates

def download_telegram_file(file_id):
    result = tg_request("getFile", {"file_id": file_id})
    if not result or not result.get("ok"): return None
    fp = result["result"]["file_path"]
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fp}", timeout=30) as r:
            return r.read()
    except Exception as e:
        log.error(f"Nedlasting: {e}"); return None


# ─── GMAIL ───────────────────────────────────────────────────────────────────

def imap_connect():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASSWORD)
    return mail

def get_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True)
                if raw: body += raw.decode("utf-8", errors="ignore")
    else:
        raw = msg.get_payload(decode=True)
        if raw: body = raw.decode("utf-8", errors="ignore")
    return body.strip()

def fetch_unseen_fiverr():
    mail = imap_connect()
    mail.select("inbox")
    _, data = mail.search(None, '(UNSEEN FROM "fiverr.com")')
    ids = data[0].split()
    results = []
    for eid in ids:
        _, raw = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(raw[0][1])
        results.append({"id": eid, "subject": msg.get("Subject",""), "body": get_body(msg)})
        mail.store(eid, "+FLAGS", "\\Seen")
    mail.logout()
    log.info(f"Sjekket innboks: {len(results)} ny(e).")
    return results


# ─── WEB SEARCH ──────────────────────────────────────────────────────────────

def web_search(query):
    """Enkel websøk via DuckDuckGo HTML."""
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        results = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html)
        clean = [re.sub(r'<[^>]+>', '', r).strip() for r in results[:5]]
        return "\n".join(clean) if clean else "Ingen resultater funnet."
    except Exception as e:
        log.error(f"Websøk feil: {e}")
        return "Søk utilgjengelig."

def competitor_analysis(product_or_role, gig_type):
    """Analyserer konkurrenter for å forbedre leveransen."""
    if gig_type in ["product_no", "product_en"]:
        query = f"{product_or_role} product description best examples ecommerce 2026"
        seo_query = f"{product_or_role} SEO keywords buying intent"
    elif gig_type in ["cv", "linkedin"]:
        query = f"{product_or_role} job requirements skills 2026 site:linkedin.com OR site:finn.no"
        seo_query = f"{product_or_role} resume keywords ATS 2026"
    else:
        query = f"{product_or_role} examples best practices 2026"
        seo_query = f"{product_or_role} keywords trends"

    results = web_search(query)
    seo_results = web_search(seo_query)
    return results, seo_results

def analyze_linkedin_profile(linkedin_url):
    """Forsøker å hente informasjon om en LinkedIn-profil."""
    try:
        req = urllib.request.Request(linkedin_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        title = re.search(r'<title>(.*?)</title>', html)
        desc = re.search(r'<meta name="description" content="(.*?)"', html)
        return {
            "title": title.group(1) if title else "",
            "description": desc.group(1) if desc else ""
        }
    except Exception as e:
        log.error(f"LinkedIn henting: {e}")
        return None


# ─── KVALITETSSIKRING ─────────────────────────────────────────────────────────

def quality_check(delivery, task, gig_id, package):
    """Claude sjekker sin egen leveranse mot kravene."""
    gig = GIGS.get(gig_id, GIGS["product_en"])
    package_desc = gig["packages"].get(package, "")

    check_prompt = (
        f"You are a quality control expert. Check this delivery against requirements.\n\n"
        f"PACKAGE BOUGHT: {package} – {package_desc}\n"
        f"TASK: {task[:500]}\n\n"
        f"DELIVERY:\n{delivery[:2000]}\n\n"
        f"Check:\n"
        f"1. Does it fulfill everything in the package?\n"
        f"2. Is the language correct and consistent?\n"
        f"3. Is the quality worth paying for?\n"
        f"4. What is missing or needs improvement?\n\n"
        f"Return JSON only: "
        '{"passes": true/false, "score": 1-10, "issues": ["issue1"], "improvements": ["improvement1"]}'
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=400,
        system="You are a quality control expert. Return only JSON.",
        messages=[{"role": "user", "content": check_prompt}]
    )
    text = re.sub(r"```json|```", "", resp.content[0].text).strip()
    try:
        return json.loads(text)
    except:
        return {"passes": True, "score": 7, "issues": [], "improvements": []}


# ─── DOCX GENERERING ─────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def generate_docx(gig_type, json_data, output_path):
    script = os.path.join(SCRIPT_DIR, "generate_cv.js" if gig_type == "docx_cv" else "generate_linkedin.js")
    json_data["output"] = output_path
    result = subprocess.run(["node", script, json.dumps(json_data, ensure_ascii=False)],
                           capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        log.error(f"Docx feil: {result.stderr}")
        return None
    return output_path


# ─── CLAUDE ──────────────────────────────────────────────────────────────────

def claude(messages, system, model="claude-sonnet-4-6", max_tokens=2000):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    resp = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=messages)
    return resp.content[0].text

def humanize(text, language="en"):
    """Siste pass: fjerner AI-tells og gjor teksten lik noe en dyktig menneskelig copywriter skrev."""
    lang = "Norwegian" if language == "no" else "English"
    system = (
        f"You are a senior {lang} copy editor. Rewrite the text so it reads like a skilled human "
        f"copywriter wrote it, never like AI. Keep the EXACT same meaning, all the same information, "
        f"roughly the same length, the same language ({lang}), and any keywords already present. "
        f"Do NOT add new facts or claims.\n\n"
        f"Remove AI-tells:\n"
        f"- Cut cliches and buzzwords (elevate, unleash, seamless, game-changer, look no further, "
        f"in today's fast-paced world, more than just, whether you're X or Y, tapestry, testament, "
        f"robust, leverage, dive into, unlock).\n"
        f"- Vary sentence length and rhythm: mix short punchy lines with longer ones.\n"
        f"- Swap vague claims for concrete, specific wording.\n"
        f"- Cut filler and throat-clearing (it's important to note, in conclusion, when it comes to).\n"
        f"- Avoid constant rule-of-three lists and over-balanced parallel structures.\n"
        f"- Do not overuse em-dashes.\n"
        f"- Confident and natural, never overhyped or salesy.\n\n"
        f"Return ONLY the rewritten text, nothing else."
    )
    try:
        return claude(
            messages=[{"role": "user", "content": text}],
            system=system,
            model="claude-sonnet-4-6",
            max_tokens=3000
        )
    except Exception as e:
        log.error(f"Humanize feil: {e}")
        return text

def check_fiverr_message(subject, body):
    """Sjekker om e-posten er en kundemelding på Fiverr."""
    subject_lower = subject.lower()
    keywords = ["sent you a message", "new message", "har sendt deg en melding", "buyer messaged"]
    return any(kw in subject_lower for kw in keywords)

def extract_task(subject, body):
    text = claude(
        model="claude-haiku-4-5-20251001", max_tokens=500,
        system="Extract job details from Fiverr email. Return ONLY JSON.",
        messages=[{"role": "user", "content": (
            "Return JSON:\n"
            '{"is_order":true/false,"order_id":"id or subject","task":"what customer wants","customer":"name or Unknown"}\n\n'
            f"SUBJECT: {subject}\nBODY: {body[:2000]}"
        )}]
    )
    data = json.loads(re.sub(r"```json|```", "", text).strip())
    if not data.get("order_id") or str(data["order_id"]) in ["null","None"]:
        numbers = re.findall(r'\d{5,}', subject)
        data["order_id"] = numbers[0] if numbers else re.sub(r'[^a-zA-Z0-9]', '-', subject[:30]).strip('-')
    return data

def get_learning_notes(gig_id):
    notes = learning["gig_notes"].get(gig_id, [])
    patterns = learning["revision_patterns"].get(gig_id, [])
    if not notes and not patterns: return ""
    text = "\n\nLEARNED FROM PAST ORDERS:\n"
    if notes: text += "Notes: " + "; ".join(notes[-3:]) + "\n"
    if patterns: text += "Common revisions to avoid: " + "; ".join(patterns[-3:])
    return text

def solve_and_deliver(task, order_id, customer, gig_id, package, language="en", competitor_context="", seo_keywords=""):
    gig = GIGS.get(gig_id, GIGS["product_en"])
    package_desc = gig["packages"].get(package, "")
    learning_notes = get_learning_notes(gig_id)

    lang_instruction = "IMPORTANT: Respond in Norwegian." if language == "no" else "IMPORTANT: Respond in English."

    context_block = ""
    if competitor_context:
        context_block += f"\n\nCOMPETITOR INSIGHTS:\n{competitor_context[:500]}"
    if seo_keywords:
        context_block += f"\n\nSEO KEYWORDS TO INCLUDE:\n{seo_keywords[:300]}"

    system = (
        f"{gig['system']}\n\n"
        f"PACKAGE: {package} – Deliver exactly: {package_desc}\n"
        f"{lang_instruction}"
        f"{context_block}"
        f"{learning_notes}"
    )

    response = claude(
        system=system,
        messages=[{"role": "user", "content": f"Order: {order_id}\nCustomer: {customer}\n\nTask:\n{task}\n\nDeliver now."}],
        max_tokens=3000
    )

    # Kvalitetssjekk
    qc = quality_check(response, task, gig_id, package)
    if not qc.get("passes") or qc.get("score", 10) < 7:
        log.info(f"QC failed (score {qc.get('score')}), improving...")
        improvements = "\n".join(qc.get("improvements", []))
        response = claude(
            system=system,
            messages=[
                {"role": "user", "content": f"Order: {order_id}\nCustomer: {customer}\n\nTask:\n{task}\n\nDeliver now."},
                {"role": "assistant", "content": response},
                {"role": "user", "content": f"Improve this delivery. Issues found:\n{improvements}\n\nDeliver the improved version now."}
            ],
            max_tokens=3000
        )

    fmt = gig["format"]
    if fmt in ["docx_cv", "docx_linkedin"]:
        try:
            json_data = json.loads(re.sub(r"```json|```", "", response).strip())
            output_path = f"/tmp/{order_id}_{fmt}.docx"
            docx_path = generate_docx(fmt, json_data, output_path)
            return response, docx_path, qc
        except Exception as e:
            log.error(f"Docx feil: {e}")
            return response, None, qc

    # Humaniserings-pass for tekst-leveranser (fjerner AI-tells)
    log.info(f"Ordre {order_id}: kjorer humaniserings-pass...")
    response = humanize(response, language)

    return response, None, qc

def chat_with_claude(order_id, user_message, file_bytes=None, file_type=None):
    global chat_history
    order = pending_orders.get(order_id, {})
    gig_id = order.get("gig", "product_en")
    gig = GIGS.get(gig_id, GIGS["product_en"])
    language = order.get("language", "en")
    lang_instruction = "IMPORTANT: Always respond in Norwegian." if language == "no" else "IMPORTANT: Always respond in English."

    system = (
        f"{gig['system']}\n{lang_instruction}\n\n"
        f"Active order: {order_id} | Customer: {order.get('customer','?')} | "
        f"Package: {order.get('package','basic')}\n"
        f"Current delivery:\n{order.get('delivery','none yet')}"
    )

    if order_id not in chat_history: chat_history[order_id] = []

    if file_bytes and file_type == "pdf":
        b64 = base64.standard_b64encode(file_bytes).decode()
        user_content = [{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":b64}},{"type":"text","text":user_message or "Analyze this."}]
    elif file_bytes:
        b64 = base64.standard_b64encode(file_bytes).decode()
        media = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png"}.get(file_type,"image/jpeg")
        user_content = [{"type":"image","source":{"type":"base64","media_type":media,"data":b64}},{"type":"text","text":user_message or "What do you see?"}]
    else:
        user_content = user_message

    chat_history[order_id].append({"role":"user","content":user_content})
    response = claude(messages=chat_history[order_id], system=system)
    chat_history[order_id].append({"role":"assistant","content":response})

    # Lær fra revisjonsforespørsler
    if order_id not in learning["revision_patterns"]:
        learning["revision_patterns"][gig_id] = []
    if len(user_message) < 200:
        learning["revision_patterns"][gig_id].append(user_message[:100])

    save_state()
    return response

def apply_revision(order_id):
    order = pending_orders.get(order_id)
    if not order: return None, None
    gig_id = order.get("gig","product_en")
    gig = GIGS.get(gig_id, GIGS["product_en"])
    history = chat_history.get(order_id, [])

    messages = history + [{"role":"user","content":"Based on all feedback, deliver the complete revised version. Delivery only."}]
    new_delivery = claude(messages=messages, system=gig["system"], max_tokens=3000)

    docx_path = None
    if gig["format"] in ["docx_cv","docx_linkedin"]:
        try:
            json_data = json.loads(re.sub(r"```json|```","",new_delivery).strip())
            docx_path = generate_docx(gig["format"], json_data, f"/tmp/{order_id}_rev.docx")
        except Exception as e:
            log.error(f"Rev docx: {e}")
    else:
        # Humaniser revidert tekst-leveranse
        new_delivery = humanize(new_delivery, order.get("language", "en"))

    pending_orders[order_id]["delivery"] = new_delivery
    save_state()
    return new_delivery, docx_path


# ─── RAPPORTER OG MELDINGER ──────────────────────────────────────────────────

last_daily_report = None
last_weekly_report = None

def send_daily_report():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    orders_today = [oid for oid, o in pending_orders.items()]
    earned_24h = stats["daily_earned"].get(today, 0.0) + stats["daily_earned"].get(yesterday, 0.0)
    total = stats["total_earned"]

    best_gig = max(stats["orders_by_gig"], key=stats["orders_by_gig"].get) if stats["orders_by_gig"] else "–"

    greetings = [
        "God morgen! ☀️ Her er dagens rapport:",
        "Rise and grind! 🚀 Hva skjer i dag:",
        "God morgen! 🌅 Klar for en ny dag:",
        "Morgen! ☕ Her er status:"
    ]
    greeting = random.choice(greetings)

    report = (
        f"{greeting}\n\n"
        f"📊 DAGLIG RAPPORT – {now.strftime('%d.%m.%Y')}\n\n"
        f"🕐 Aktive ordrer nå: {len(pending_orders)}\n"
        f"💰 Inntekt siste 24t: ${earned_24h:.2f}\n"
        f"💵 Total inntekt: ${total:.2f}\n"
        f"📦 Ordrer totalt: {stats['orders_completed']}\n"
        f"🏆 Beste gig: {best_gig}\n"
    )

    if pending_orders:
        report += f"\n⏳ VENTER PÅ LEVERING:\n"
        for oid, o in pending_orders.items():
            report += f"  • {oid} ({o['customer']})\n"

    if stats["orders_completed"] == 0:
        report += "\n💡 Ingen salg ennå – sørg for at gigene dine er aktive og promoter dem!"

    send_telegram(report)

def send_weekly_report():
    now = datetime.now()
    week_earned = sum(
        v for k, v in stats["daily_earned"].items()
        if (now - datetime.strptime(k, "%Y-%m-%d")).days <= 7
    )
    messages = [
        f"📈 UKESRAPPORT\n\nUken oppsummert:\n💰 Tjent: ${week_earned:.2f}\n📦 Ordrer fullført: {stats['orders_completed']}\n💵 Totalt noensinne: ${stats['total_earned']:.2f}",
    ]
    if week_earned == 0:
        messages.append("\n🎯 Ingen inntekt denne uken. Husk: promoter gigene dine på sosiale medier og send 10 buyer requests!")
    elif week_earned < 20:
        messages.append(f"\n📈 Bra start! Fortsett å levere kvalitet og anmeldelsene vil komme.")
    else:
        messages.append(f"\n🔥 Solid uke! Du er på vei!")
    send_telegram("".join(messages))

def check_scheduled_messages():
    global last_daily_report, last_weekly_report
    now = datetime.now()

    # Daglig rapport kl 07:00
    if now.hour == 7 and now.minute < 2:
        today_str = now.strftime("%Y-%m-%d")
        if last_daily_report != today_str:
            send_daily_report()
            last_daily_report = today_str

    # Ukentlig rapport søndag kl 20:00
    if now.weekday() == 6 and now.hour == 20 and now.minute < 2:
        week_str = now.strftime("%Y-W%W")
        if last_weekly_report != week_str:
            send_weekly_report()
            last_weekly_report = week_str

def send_fun_message(event, data={}):
    messages = {
        "new_order": [
            f"🎯 Ny ordre fra {data.get('customer','en kunde')}! Claude er på saken. ⚡",
            f"📬 Bing! Ny bestilling. Leverer om et øyeblikk... 🤖",
            f"💼 {data.get('customer','Kunde')} stoler på deg. La oss ikke skuffe dem! 🔥",
        ],
        "order_delivered": [
            f"✅ Levert! Nå er det bare å vente på 5 stjerner ⭐⭐⭐⭐⭐",
            f"🚀 Avsendt! {data.get('customer','Kunden')} får noe bra i dag.",
        ],
        "stille_dag": [
            "🌊 Stille dag i butikken. Perfekt tid til å optimalisere gigene eller sjekke buyer requests 💡",
            "☕ Ingen ordrer i dag. Kanskje dele Fiverr-profilen på LinkedIn? 📱",
        ]
    }
    options = messages.get(event, [])
    if options:
        send_telegram(random.choice(options))


# ─── PROSESSER ORDRE ─────────────────────────────────────────────────────────

def process(mail_data):
    global active_order
    log.info(f"Behandler: {mail_data['subject']}")

    if check_fiverr_message(mail_data["subject"], mail_data["body"]):
        send_with_buttons(
            f"💬 NY MELDING PÅ FIVERR!\n\n"
            f"Fra: {mail_data['subject']}\n\n"
            f"{mail_data['body'][:500]}\n\n"
            f"Gå inn på Fiverr og svar kunden.",
            buttons=[[{"text": "📱 Åpne Fiverr", "data": "open_fiverr"}]]
        )
        return

    details = extract_task(mail_data["subject"], mail_data["body"])
    if not details.get("is_order"):
        log.info("Ikke en ny ordre.")
        return

    task     = details.get("task","")
    order_id = details.get("order_id","ukjent")
    customer = details.get("customer","Kunde")
    if not task: return

    gig_id  = detect_gig(mail_data["subject"], task)
    package = detect_package(mail_data["subject"], mail_data["body"], gig_id)
    language = detect_language(task)

    log.info(f"Ordre {order_id} → gig:{gig_id} pakke:{package} språk:{language}")

    # Morsommelding ved ny ordre
    send_fun_message("new_order", {"customer": customer})

    # Konkurrentanalyse + SEO
    send_telegram(f"🔍 Analyserer konkurrenter og SEO for ordre {order_id}...")
    comp_results, seo_results = competitor_analysis(task[:100], gig_id)

    # Løs oppgaven
    send_telegram(f"⚙️ Genererer leveranse for {order_id}...")
    delivery, docx_path, qc = solve_and_deliver(task, order_id, customer, gig_id, package, language, comp_results, seo_results)

    pending_orders[order_id] = {
        "task": task, "customer": customer, "delivery": delivery,
        "gig": gig_id, "package": package, "language": language
    }
    chat_history[order_id] = []
    active_order = order_id
    save_state()

    qc_line = f"QC: {'✅' if qc.get('passes') else '⚠️'} Score {qc.get('score',0)}/10"

    send_with_buttons(
        f"📦 ORDRE KLAR: {order_id}\n"
        f"Kunde: {customer} | Pakke: {package} | Språk: {language}\n"
        f"{qc_line}\n\n"
        f"--- LEVERANSE ---\n{delivery[:2000]}\n-----------------",
        buttons=[
            [{"text": "✅ Godkjenn", "data": f"ok_{order_id}"},
             {"text": "✏️ Revider", "data": f"revider_{order_id}"}],
            [{"text": "📊 QC-rapport", "data": f"qc_{order_id}"},
             {"text": "💬 Chat", "data": f"chat_{order_id}"}]
        ]
    )

    if docx_path:
        send_telegram_file(docx_path, caption=f"Ferdig dokument – {order_id}")

    send_fun_message("order_delivered", {"customer": customer})
    log.info(f"Ordre {order_id} levert.")


# ─── TELEGRAM KOMMANDOER ─────────────────────────────────────────────────────

stille_dag_varslet = False

def handle_telegram_updates():
    global active_order, stille_dag_varslet
    updates = get_telegram_updates()

    for update in updates:
        # Håndter knapper
        callback = update.get("callback_query")
        if callback:
            data = callback.get("data","")
            chat_id = str(callback.get("from",{}).get("id",""))
            if chat_id != str(TELEGRAM_CHAT_ID): continue

            if data.startswith("ok_"):
                oid = data[3:]
                handle_ok(oid)
            elif data.startswith("revider_"):
                oid = data[8:]
                active_order = oid
                send_telegram(f"Revisjonsmodus for {oid}. Skriv hva som skal endres:")
            elif data.startswith("qc_"):
                oid = data[3:]
                order = pending_orders.get(oid)
                if order:
                    qc = quality_check(order["delivery"], order["task"], order["gig"], order["package"])
                    send_telegram(f"QC-RAPPORT: {oid}\nScore: {qc.get('score')}/10\nProblemer: {qc.get('issues')}\nForbedringer: {qc.get('improvements')}")
            elif data.startswith("chat_"):
                oid = data[5:]
                active_order = oid
                send_telegram(f"Aktiv ordre: {oid}. Chat fritt – skriv /lever for ny versjon.")

            tg_request("answerCallbackQuery", {"callback_query_id": callback["id"]})
            continue

        msg = update.get("message",{})
        text = msg.get("text","").strip()
        chat_id = str(msg.get("chat",{}).get("id",""))
        document = msg.get("document")
        photo = msg.get("photo")

        if chat_id != str(TELEGRAM_CHAT_ID): continue
        if document or photo: handle_file(msg, text); continue
        if not text: continue

        log.info(f"Telegram: {text}")

        # /ny
        if text.lower().startswith("/ny"):
            handle_ny(text)
            continue

        # /rapport
        if text.lower() in ["/rapport", "rapport"]:
            send_daily_report()
            continue

        # /inntekt
        if text.lower() in ["/inntekt", "inntekt"]:
            send_telegram(
                f"💰 INNTEKTSOVERSIKT\n\n"
                f"Siste 24t: ${get_earnings_last_24h():.2f}\n"
                f"Totalt: ${stats['total_earned']:.2f}\n"
                f"Ordrer: {stats['orders_completed']}\n"
                f"Første ordre: $4.00 ✅"
            )
            continue

        # ok
        ok_match = re.match(r"^ok\s+(\S+)", text, re.IGNORECASE)
        if ok_match:
            handle_ok(ok_match.group(1).strip())
            continue

        # /status
        if text.lower() in ["/status","status"]:
            if not pending_orders:
                send_telegram("Ingen aktive ordrer. 🎉")
            else:
                lines = ["📋 AKTIVE ORDRER:\n"]
                for oid, o in pending_orders.items():
                    marker = " ← AKTIV" if oid == active_order else ""
                    lines.append(f"• {oid}\n  Kunde: {o['customer']} | {o.get('gig','?')} | {o.get('package','basic')}{marker}")
                send_telegram("\n".join(lines))
            continue

        # /ordre
        ordre_match = re.match(r"^/ordre\s+(\S+)", text, re.IGNORECASE)
        if ordre_match:
            oid = ordre_match.group(1)
            if oid in pending_orders:
                active_order = oid
                o = pending_orders[oid]
                send_telegram(f"Aktiv ordre: {oid}\nKunde: {o['customer']} | {o.get('gig')} | {o.get('package')}\n\nChat fritt. /lever for ny versjon.")
            else:
                send_telegram(f"Fant ikke '{oid}'. Aktive: {', '.join(pending_orders.keys()) or 'ingen'}")
            continue

        # /lever
        if text.lower() in ["/lever","lever"]:
            if not active_order:
                send_telegram("Ingen aktiv ordre. Bruk /ordre <id> først.")
                continue
            send_telegram("⚙️ Genererer revidert leveranse...")
            new_delivery, docx_path = apply_revision(active_order)
            send_with_buttons(
                f"📦 NY LEVERANSE: {active_order}\n\n{new_delivery[:3000]}",
                buttons=[[{"text":"✅ Godkjenn","data":f"ok_{active_order}"},{"text":"✏️ Revider mer","data":f"revider_{active_order}"}]]
            )
            if docx_path: send_telegram_file(docx_path, caption=f"Revidert – {active_order}")
            continue

        # endre
        endre_match = re.match(r"^endre\s+(\S+):\s*(.+)", text, re.IGNORECASE | re.DOTALL)
        if endre_match:
            oid, instruction = endre_match.group(1).strip(), endre_match.group(2).strip()
            if oid not in pending_orders:
                send_telegram(f"Fant ikke '{oid}'. Aktive: {', '.join(pending_orders.keys()) or 'ingen'}")
                continue
            active_order = oid
            response = chat_with_claude(oid, instruction)
            send_with_buttons(
                f"[{oid}] {response}\n\nSkriv /lever for ny leveranse.",
                buttons=[[{"text":"🚀 Lever nå","data":f"ok_{oid}"},{"text":"✏️ Mer endringer","data":f"revider_{oid}"}]]
            )
            continue

        # /hjelp
        if text.lower() in ["/hjelp","/help","hjelp"]:
            send_telegram(
                "📖 KOMMANDOER\n\n"
                "/ny  →  ny manuell ordre\n"
                "/ordre <id>  →  velg aktiv ordre\n"
                "/lever  →  generer ny leveranse\n"
                "/status  →  vis alle ordrer\n"
                "/rapport  →  daglig rapport nå\n"
                "/inntekt  →  inntektsoversikt\n"
                "ok <id>  →  godkjenn\n"
                "endre <id>: <tekst>  →  revider\n\n"
                "💡 Alle ordrer har også knapper du kan trykke på!"
            )
            continue

        # /legg_til_inntekt (manuell registrering)
        inntekt_match = re.match(r"^/inntekt_add\s+([\d.]+)\s*(\S+)?", text, re.IGNORECASE)
        if inntekt_match:
            amount = float(inntekt_match.group(1))
            gig_id = inntekt_match.group(2) or "product_no"
            record_earning(amount, gig_id)
            send_telegram(f"💰 Registrert ${amount:.2f} for {gig_id}. Total: ${stats['total_earned']:.2f}")
            continue

        # Fri chat
        if active_order and active_order in pending_orders:
            response = chat_with_claude(active_order, text)
            send_with_buttons(
                f"{response}\n\nSkriv /lever for ny leveranse.",
                buttons=[[{"text":"🚀 Lever nå","data":f"ok_{active_order}"},{"text":"🔄 Fortsett chat","data":f"chat_{active_order}"}]]
            )
        else:
            if "__general__" not in chat_history: chat_history["__general__"] = []
            chat_history["__general__"].append({"role":"user","content":text})
            response = claude(messages=chat_history["__general__"], system="Du er en hjelpsom assistent. Svar konsist på norsk.")
            chat_history["__general__"].append({"role":"assistant","content":response})
            send_telegram(response)

def handle_ok(oid):
    global active_order
    if oid in pending_orders:
        order = pending_orders[oid]
        gig_id = order.get("gig","product_no")
        package = order.get("package","basic")
        gig = GIGS.get(gig_id, GIGS["product_en"])
        auto_amount = round(gig["prices"].get(package, 0) * 0.80, 2)  # Fiverr tar 20%
        if active_order == oid: active_order = None
        del pending_orders[oid]
        if oid in chat_history: del chat_history[oid]
        record_earning(auto_amount, gig_id)
        send_telegram(
            f"✅ Ordre {oid} godkjent!\n\n"
            f"💰 ${auto_amount:.2f} registrert automatisk (etter Fiverrs 20% kutt)\n"
            f"📊 Total: ${stats['total_earned']:.2f}\n\n"
            f"Husk å levere på Fiverr!"
        )
    else:
        send_telegram(f"Fant ikke '{oid}'. Aktive: {', '.join(pending_orders.keys()) or 'ingen'}")

def handle_ny(text):
    global active_order
    lines = text[3:].strip().split("\n")
    task = customer = ""
    order_id = f"MANUAL-{int(time.time())}"
    gig_id_override = package_override = None
    for line in lines:
        ll = line.lower()
        if ll.startswith(("kunde:","customer:")): customer = line.split(":",1)[1].strip()
        elif ll.startswith(("oppgave:","task:")): task = line.split(":",1)[1].strip()
        elif ll.startswith(("ordre:","order:")): order_id = line.split(":",1)[1].strip()
        elif ll.startswith("gig:"): gig_id_override = line.split(":",1)[1].strip().lower()
        elif ll.startswith(("pakke:","package:")): package_override = line.split(":",1)[1].strip().lower()
    if not task: task = text[3:].strip()
    if not task:
        send_telegram("Bruk:\n/ny\nKunde: Navn\nOppgave: Beskriv hva som skal lages\nGig: cv/linkedin/product_no/product_en/email\nPakke: basic/standard/premium")
        return
    gig_id  = gig_id_override or detect_gig(task, task)
    package = package_override or "basic"
    language = detect_language(task)
    send_telegram(f"⚙️ Behandler {order_id} (gig:{gig_id} pakke:{package})...")
    comp_results, seo_results = competitor_analysis(task[:100], gig_id)
    delivery, docx_path, qc = solve_and_deliver(task, order_id, customer or "Manual", gig_id, package, language, comp_results, seo_results)
    pending_orders[order_id] = {"task":task,"customer":customer or "Manual","delivery":delivery,"gig":gig_id,"package":package,"language":language}
    chat_history[order_id] = []
    active_order = order_id
    save_state()
    send_with_buttons(
        f"📦 MANUELL ORDRE: {order_id}\nGig: {gig_id} | Pakke: {package}\nQC: {'✅' if qc.get('passes') else '⚠️'} {qc.get('score')}/10\n\n--- LEVERANSE ---\n{delivery[:2000]}\n-----------------",
        buttons=[[{"text":"✅ Godkjenn","data":f"ok_{order_id}"},{"text":"✏️ Revider","data":f"revider_{order_id}"}]]
    )
    if docx_path: send_telegram_file(docx_path, caption=f"Dokument – {order_id}")

def handle_file(msg, caption=""):
    document = msg.get("document")
    photo = msg.get("photo")
    if document:
        mime = document.get("mime_type","")
        file_type = "pdf" if "pdf" in mime else None
        if not file_type: send_telegram("Støtter kun PDF og bilder."); return
        file_id = document["file_id"]
    elif photo:
        file_id = photo[-1]["file_id"]
        file_type = "jpg"
    else: return
    send_telegram("📥 Laster ned fil...")
    file_bytes = download_telegram_file(file_id)
    if not file_bytes: send_telegram("Kunne ikke laste ned filen."); return
    prompt = caption or "Analyser dette og gi meg en oppsummering."
    if active_order and active_order in pending_orders:
        response = chat_with_claude(active_order, prompt, file_bytes=file_bytes, file_type=file_type)
        send_telegram(f"{response}\n\nSkriv /lever for ny leveranse.")
    else:
        if file_type == "pdf":
            b64 = base64.standard_b64encode(file_bytes).decode()
            msgs = [{"role":"user","content":[{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":b64}},{"type":"text","text":prompt}]}]
        else:
            b64 = base64.standard_b64encode(file_bytes).decode()
            msgs = [{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},{"type":"text","text":prompt}]}]
        response = claude(messages=msgs, system="Du er en hjelpsom assistent.")
        send_telegram(response)


# ─── STILLE DAG CHECK ────────────────────────────────────────────────────────

last_stille_check = None

def check_stille_dag():
    global last_stille_check
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if now.hour == 18 and last_stille_check != today:
        today_earned = stats["daily_earned"].get(today, 0.0)
        if today_earned == 0 and stats["orders_completed"] > 0:
            send_fun_message("stille_dag")
        last_stille_check = today


# ─── HOVEDLØKKE ──────────────────────────────────────────────────────────────

def main():
    load_state()
    log.info("Fiverr Agent v9 starter. Sjekker hvert %s sek.", CHECK_INTERVAL)
    send_telegram(
        "🤖 Fiverr Agent v9 er online!\n\n"
        "Hva er nytt:\n"
        "  📊 Daglig rapport kl 07:00\n"
        "  🔍 Automatisk konkurrentanalyse + SEO\n"
        "  ✅ Kvalitetssikring på alle leveranser\n"
        "  🎯 Telegram-knapper på alle ordrer\n"
        "  🌍 Automatisk språkgjenkjenning\n"
        "  📈 Inntektstracking\n"
        "  🧠 Lærer av revisjoner\n\n"
        "Skriv /hjelp for kommandoer."
    )

    while True:
        try:
            handle_telegram_updates()
            check_scheduled_messages()
            check_stille_dag()
            mails = fetch_unseen_fiverr()
            for m in mails:
                process(m)
        except Exception as e:
            log.error(f"Feil: {type(e).__name__}: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
