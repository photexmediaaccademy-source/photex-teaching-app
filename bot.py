# photex_media_service_team.py
# ------------------------------------------------------------
# Photex Media Service Team — Unique training mentor on Telegram
# Features:
# - Registration with FULL NAME + auto student code
# - 4-day Free Basics + 8-day Advanced (preloaded curriculum)
# - Trainer-controlled pinning (/pin_today, /pin_custom)
# - Submissions, grading, approvals, pauses/resumes
# - Progress reports, pending lists
# - Awards: /star, /bonus; auto-congrats on approvals & capstone
# - Certificates (image via Pillow) + Graduation (/certify, /graduate)
# - Human mentor tone (never says “I’m a bot”)
# ------------------------------------------------------------

import os
import sqlite3
import logging
import textwrap
from datetime import datetime, timedelta
from typing import Optional, Tuple

from telegram import (
    Update, ChatPermissions, InputMediaPhoto, InputFile,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

# Try to import Pillow for certificate creation
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except Exception:
    PIL_OK = False

# --------------------- CONFIG ---------------------
BOT_TOKEN = "8313135492:AAEYPvtV_M7M4uUObtZBF1TUlqDBzJatDGE"
DB_FILE = "photex.db"

# Put numeric Telegram IDs here
ADMIN_IDS = {1234567890}         # admins can certify/graduate/edit everything
TRAINER_IDS = {1234567890}       # trainers can pin/mark/approve
GROUP_ID = -1001234567890        # your training group chat id (bot must be admin)

# Free basics length
BASICS_DAYS = 4
TOTAL_DAYS = 12

# Toggle: send full advanced lesson only to approved in DM
DM_ADVANCED_TO_APPROVED = True

# --------------------- LOGGING ---------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    level=logging.INFO
)
log = logging.getLogger("photex")

# --------------------- DB HELPERS ---------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE,
        full_name TEXT,
        code TEXT UNIQUE,
        status TEXT DEFAULT 'basic', -- basic | advanced | paused | graduated
        interest TEXT,
        joined TIMESTAMP,
        graduated INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS lessons(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day_num INTEGER,          -- 1..12
        tier TEXT,                -- basic | advanced
        title TEXT,
        content TEXT,
        assignment TEXT,
        deadline_hour INTEGER DEFAULT 21 -- 21 = 9PM
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_code TEXT,
        lesson_day INTEGER,
        file_id TEXT,
        submitted TIMESTAMP,
        grade INTEGER,
        feedback TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS awards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_code TEXT,
        lesson_day INTEGER,
        kind TEXT,        -- star | bonus | winner
        created TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    conn.commit()
    conn.close()

def set_setting(key: str, value: str):
    conn = db()
    conn.execute("REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    conn.commit()
    conn.close()

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def preload_curriculum():
    """Insert lessons if table empty."""
    conn = db()
    existing = conn.execute("SELECT COUNT(*) c FROM lessons").fetchone()["c"]
    if existing > 0:
        conn.close()
        return

    basics = [
        (1, "basic", "Welcome to Photex Academy – Free Basics (Day 1)",
         "Media is power: stories, brands, visuals. This week is a TASTE: "
         "Photography & Photoshop fundamentals. Limited lessons to warm you up.\n\n"
         "When you subscribe to Advanced, you unlock:\n"
         "• Videography & Cinematic Shooting\n"
         "• Editing & Professional Color Grading\n"
         "• Advanced Graphics & Branding\n"
         "• Portrait & Product Photography\n"
         "• Social Media Marketing & Business Skills\n"
         "• CERTIFICATE at the end\n\n"
         "Who you become: a Creative Professional able to design, shoot, edit, and market for real clients.",
         "Introduce yourself: your full name + one reason you joined."),
        (2, "basic", "Photoshop Basics I – Workspace & Layers (Day 2)",
         "Get comfortable: interface, tools panel, moving layers, text tool, saving files.\n"
         "Tip: Name your layers. Simplicity = speed.",
         "Open an image and add your name with the Text Tool. Export and submit."),
        (3, "basic", "Photography Basics I – Composition (Day 3)",
         "Rule of Thirds, framing, perspective, leading lines. Light: face the light, then shape it.",
         "Capture a photo using Rule of Thirds. Submit your best shot."),
        (4, "basic", "Photoshop Basics II – Color & Retouch (Day 4)",
         "Exposure, white balance, curves; Spot Healing for quick cleanup. Keep skin natural.",
         "Retouch the provided sample (or your own portrait). Export and submit.")
    ]

    advanced = [
        (5, "advanced", "Videography I – Cinematic Shooting",
         "Angles, camera movement (pan/tilt/dolly), storyboarding short sequences. Keep shots steady.",
         "Shoot a 15s cinematic clip using at least one movement (pan/tilt/dolly)."),
        (6, "advanced", "Photography Advanced – Portraits & Products",
         "Lighting setups (key/fill/back), posing basics, backgrounds, reflections. Control highlights.",
         "Submit one portrait + one product photo (clean background)."),
        (7, "advanced", "Videography II – Editing Workflow",
         "Cutting, pacing, continuity. Tell a story with 30 seconds. Music supports emotion.",
         "Edit provided raw clips (or yours) into a 30s micro-story."),
        (8, "advanced", "Editing & Color Grading",
         "Color correction vs grading, LUTs, matching shots. Mood through color.",
         "Apply a cinematic grade to a short clip. Export and submit."),
        (9, "advanced", "Photoshop Advanced – Branding & Posters",
         "Visual hierarchy, grids, typography pairing, brand consistency.",
         "Design a poster for a youth event using given assets or your own."),
        (10, "advanced", "Digital Marketing & Content Strategy",
         "Audience, message, format, schedule. Hooks, captions, CTAs. Measure what matters.",
         "Draft a 1-week social campaign plan for a small brand."),
        (11, "advanced", "3D & Product Visualization (Intro)",
         "Concepts: modeling vs mockups; product scenes with depth, shadows, realism.",
         "Create a branded mockup (template allowed) and submit your render."),
        (12, "advanced", "Capstone + Graduation",
         "Combine skills: shoot or source a photo → edit → design a poster → write a caption.",
         "Submit your final integrated project.")
    ]

    cur = conn.cursor()
    for d, tier, title, content, assign in basics + advanced:
        cur.execute(
            "INSERT INTO lessons(day_num,tier,title,content,assignment,deadline_hour) VALUES(?,?,?,?,?,?)",
            (d, tier, title, content, assign, 21)
        )
    conn.commit()
    conn.close()

# --------------------- UTIL ---------------------
def is_trainer(user_id: int) -> bool:
    return user_id in TRAINER_IDS or user_id in ADMIN_IDS

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def next_student_code(conn: sqlite3.Connection) -> str:
    yy = datetime.now().year
    # Create a sequential number per year
    row = conn.execute("SELECT COUNT(*) c FROM students WHERE strftime('%Y', joined)=?", (str(yy),)).fetchone()
    seq = row["c"] + 1
    return f"PHX-{yy}-{seq:04d}"

def get_student_by_tg(tg_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    row = conn.execute("SELECT * FROM students WHERE tg_id=?", (tg_id,)).fetchone()
    conn.close()
    return row

def get_student_by_code(code: str) -> Optional[sqlite3.Row]:
    conn = db()
    row = conn.execute("SELECT * FROM students WHERE code=?", (code,)).fetchone()
    conn.close()
    return row

def must_be_trainer(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_trainer(uid):
            await update.effective_message.reply_text("Photex Team: trainer access required.")
            return
        return await func(update, context)
    return wrapper

def must_be_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_admin(uid):
            await update.effective_message.reply_text("Photex Team: admin access required.")
            return
        return await func(update, context)
    return wrapper

# --------------------- REGISTRATION ---------------------
ASK_FULLNAME = 10

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    st = get_student_by_tg(user.id)
    if st:
        await update.message.reply_text(
            f"👋 Welcome back, {st['full_name']}!\n"
            f"Your Student Code: {st['code']}\n"
            f"Status: {st['status'].upper()}.\n\n"
            "Photex Media Service Team is here to guide you—submit on time and keep learning."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Welcome to Photex Media Academy (Free Basics – 4 Days)\n\n"
        "To register officially, send your FULL NAME (as it should appear on your certificate)."
    )
    return ASK_FULLNAME

async def capture_fullname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = update.message.text.strip()
    if len(full_name) < 3 or " " not in full_name:
        await update.message.reply_text("Please send your FULL NAME (first + last).")
        return ASK_FULLNAME

    conn = db()
    code = next_student_code(conn)
    conn.execute(
        "INSERT INTO students(tg_id,full_name,code,status,joined) VALUES(?,?,?,?,?)",
        (update.effective_user.id, full_name, code, "basic", datetime.now())
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Registered!\n\n"
        f"Name: {full_name}\n"
        f"Student Code: {code}\n\n"
        f"📚 You’re enrolled in *Free Basics (4 Days)*.\n"
        f"Trainers will lead live sessions. Submit assignments on time to earn approval to Advanced."
    )
    return ConversationHandler.END

async def cancel_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Registration canceled. You can /start anytime.")
    return ConversationHandler.END

# --------------------- PINNING & LESSONS ---------------------
@must_be_trainer
async def pin_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trainer pins today's lesson based on cohort day."""
    # Cohort day tracking
    day_str = get_setting("cohort_day", "1")
    day = int(day_str)
    lesson = get_lesson(day)
    if not lesson:
        await update.message.reply_text("No lesson found for today.")
        return

    text = lesson_message_preview(lesson)
    msg = await context.bot.send_message(
        chat_id=GROUP_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        await context.bot.pin_chat_message(chat_id=GROUP_ID, message_id=msg.message_id, disable_notification=True)
    except Exception:
        pass

    # DM full advanced content to approved students (optional)
    if lesson["tier"] == "advanced" and DM_ADVANCED_TO_APPROVED:
        await dm_advanced_to_approved(context, lesson)

@must_be_trainer
async def pin_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /pin_custom DAY_NUMBER (1-12)")
        return
    try:
        day = int(context.args[0])
    except Exception:
        await update.message.reply_text("Day must be a number 1–12.")
        return

    lesson = get_lesson(day)
    if not lesson:
        await update.message.reply_text("No lesson found for that day.")
        return

    text = lesson_message_preview(lesson)
    msg = await context.bot.send_message(GROUP_ID, text, parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.pin_chat_message(chat_id=GROUP_ID, message_id=msg.message_id, disable_notification=True)
    except Exception:
        pass

    if lesson["tier"] == "advanced" and DM_ADVANCED_TO_APPROVED:
        await dm_advanced_to_approved(context, lesson)

def get_lesson(day: int) -> Optional[sqlite3.Row]:
    conn = db()
    row = conn.execute("SELECT * FROM lessons WHERE day_num=?", (day,)).fetchone()
    conn.close()
    return row

def lesson_message_preview(lesson: sqlite3.Row) -> str:
    # Group-friendly, concise; for advanced we post teaser
    header = f"📚 *Lesson {lesson['day_num']}: {lesson['title']}*"
    if lesson["tier"] == "advanced" and DM_ADVANCED_TO_APPROVED:
        body = "This is an Advanced lesson. Full materials have been sent privately to approved students.\n\n"
    else:
        body = f"{lesson['content']}\n\n"
    footer = f"📝 *Assignment:* {lesson['assignment']}\n" \
             f"⏰ *Deadline:* Today {lesson['deadline_hour']}:00\n" \
             f"📨 Submit with: `/submit {lesson['day_num']}` + attach file"
    return f"{header}\n\n{body}{footer}"

async def dm_advanced_to_approved(context: ContextTypes.DEFAULT_TYPE, lesson: sqlite3.Row):
    conn = db()
    rows = conn.execute("SELECT tg_id, full_name FROM students WHERE status='advanced' AND graduated=0").fetchall()
    conn.close()
    text = (
        f"📚 *Advanced Lesson {lesson['day_num']}: {lesson['title']}*\n\n"
        f"{lesson['content']}\n\n"
        f"📝 *Assignment:* {lesson['assignment']}\n"
        f"⏰ *Deadline:* Today {lesson['deadline_hour']}:00\n"
        f"📨 Submit with: `/submit {lesson['day_num']}` + attach file"
    )
    for r in rows:
        try:
            await context.bot.send_message(chat_id=r["tg_id"], text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            continue

# --------------------- COHORT DAY CONTROL ---------------------
@must_be_trainer
async def day_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur = int(get_setting("cohort_day", "1"))
    if cur >= TOTAL_DAYS:
        await update.message.reply_text(f"Cohort day is already at {TOTAL_DAYS}.")
        return
    set_setting("cohort_day", str(cur + 1))
    await update.message.reply_text(f"✅ Cohort day advanced to {cur + 1}.")

@must_be_trainer
async def day_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /day_set N (1-12)")
        return
    try:
        n = int(context.args[0])
    except Exception:
        await update.message.reply_text("Day must be a number 1–12.")
        return
    if not 1 <= n <= TOTAL_DAYS:
        await update.message.reply_text("Day must be between 1 and 12.")
        return
    set_setting("cohort_day", str(n))
    await update.message.reply_text(f"✅ Cohort day set to {n}.")

# --------------------- SUBMISSIONS ---------------------
async def submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    st = get_student_by_tg(user.id)
    if not st:
        await update.message.reply_text("Please /start to register first.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /submit DAY_NUMBER and attach your file.")
        return
    try:
        day = int(context.args[0])
    except Exception:
        await update.message.reply_text("Day must be a number.")
        return

    # Check attachments
    msg = update.message
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document:
        file_id = msg.document.file_id
    elif msg.video:
        file_id = msg.video.file_id

    if not file_id:
        await update.message.reply_text("Attach a photo/document/video with your /submit command.")
        return

    # Enforce advanced access
    lesson = get_lesson(day)
    if lesson and lesson["tier"] == "advanced" and st["status"] != "advanced":
        await update.message.reply_text(
            "This is an Advanced assignment. Please complete Basics and get trainer approval to continue."
        )
        return

    conn = db()
    conn.execute(
        "INSERT INTO submissions(student_code,lesson_day,file_id,submitted) VALUES(?,?,?,?)",
        (st["code"], day, file_id, datetime.now())
    )
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ Submission received. Photex trainers will review and respond.")
    # Notify trainers in group
    try:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"📩 Submission: {st['full_name']} ({st['code']}) for Lesson {day}."
        )
    except Exception:
        pass

# --------------------- GRADING & APPROVALS ---------------------
@must_be_trainer
async def mark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /mark CODE DAY SCORE feedback...
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /mark CODE DAY SCORE [feedback...]")
        return
    code = context.args[0]
    try:
        day = int(context.args[1])
        score = int(context.args[2])
    except Exception:
        await update.message.reply_text("DAY and SCORE must be numbers.")
        return
    feedback = " ".join(context.args[3:]) if len(context.args) > 3 else ""

    conn = db()
    row = conn.execute(
        "SELECT id FROM submissions WHERE student_code=? AND lesson_day=? ORDER BY submitted DESC",
        (code, day)
    ).fetchone()
    if not row:
        conn.close()
        await update.message.reply_text("No submission found for that student/day.")
        return
    conn.execute("UPDATE submissions SET grade=?, feedback=? WHERE id=?", (score, feedback, row["id"]))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Marked {code} for Lesson {day}: {score}.")
    # DM student feedback
    st = get_student_by_code(code)
    if st:
        try:
            await context.bot.send_message(
                chat_id=st["tg_id"],
                text=f"🏆 Lesson {day} feedback:\nScore: {score}\n{feedback}"
            )
        except Exception:
            pass

@must_be_trainer
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /approve CODE")
        return
    code = context.args[0].strip()
    conn = db()
    conn.execute("UPDATE students SET status='advanced' WHERE code=?", (code,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🎓 {code} has been approved for Advanced Training.")
    # Group congrats
    try:
        await context.bot.send_message(GROUP_ID, f"🎓 {code} has unlocked *Advanced Training*! Welcome to the pro track! 👏")
    except Exception:
        pass
    # DM advanced welcome
    st = get_student_by_code(code)
    if st:
        try:
            await context.bot.send_message(
                chat_id=st["tg_id"],
                text="🚀 Welcome to Advanced Training!\nYou now receive full lessons, professional assignments, and a CERTIFICATE upon completion."
            )
        except Exception:
            pass

@must_be_trainer
async def pause_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /pause CODE")
        return
    code = context.args[0]
    conn = db()
    conn.execute("UPDATE students SET status='paused' WHERE code=?", (code,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"⏸️ {code} has been paused.")

@must_be_trainer
async def resume_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /resume CODE")
        return
    code = context.args[0]
    conn = db()
    conn.execute("UPDATE students SET status='basic' WHERE code=? AND status='paused'", (code,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"▶️ {code} has been resumed (Basics).")

# --------------------- REPORTS ---------------------
@must_be_trainer
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    rows = conn.execute("SELECT full_name, code, status FROM students WHERE graduated=0 ORDER BY joined").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No students yet.")
        return
    lines = [f"📊 *Class Report* ({len(rows)} students)"]
    for r in rows:
        lines.append(f"• {r['full_name']} ({r['code']}) – {r['status']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@must_be_trainer
async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /pending DAY
    if not context.args:
        await update.message.reply_text("Usage: /pending DAY")
        return
    day = int(context.args[0])
    conn = db()
    # who submitted
    submitted = {r["student_code"] for r in conn.execute(
        "SELECT DISTINCT student_code FROM submissions WHERE lesson_day=?", (day,)).fetchall()}
    # all students eligible for that tier
    les = get_lesson(day)
    if not les:
        await update.message.reply_text("Invalid lesson day.")
        conn.close()
        return
    if les["tier"] == "basic":
        all_rows = conn.execute("SELECT code FROM students WHERE status IN('basic','advanced') AND graduated=0").fetchall()
    else:
        all_rows = conn.execute("SELECT code FROM students WHERE status='advanced' AND graduated=0").fetchall()
    conn.close()
    all_codes = [r["code"] for r in all_rows]
    missing = [c for c in all_codes if c not in submitted]
    if missing:
        await update.message.reply_text("⏰ Missing submissions:\n" + ", ".join(missing))
    else:
        await update.message.reply_text("✅ Everyone submitted!")

# --------------------- AWARDS & MOTIVATION ---------------------
@must_be_trainer
async def star(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /star CODE DAY   -> Student of the Day
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /star CODE DAY")
        return
    code = context.args[0]; day = int(context.args[1])
    conn = db()
    conn.execute("INSERT INTO awards(student_code,lesson_day,kind,created) VALUES(?,?,?,?)",
                 (code, day, "star", datetime.now()))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🌟 Awarded Student of the Day: {code} for Lesson {day}.")
    try:
        await context.bot.send_message(GROUP_ID, f"🏅 Congratulations! {code} is *Student of the Day* for Lesson {day}. Keep inspiring the class! 🚀")
    except Exception:
        pass

@must_be_trainer
async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /bonus CODE  -> Surprise bonus
    if not context.args:
        await update.message.reply_text("Usage: /bonus CODE")
        return
    code = context.args[0]
    conn = db()
    conn.execute("INSERT INTO awards(student_code,lesson_day,kind,created) VALUES(?,?,?,?)",
                 (code, 0, "bonus", datetime.now()))
    conn.commit()
    conn.close()
    st = get_student_by_code(code)
    if st:
        try:
            await context.bot.send_message(
                st["tg_id"],
                "🎁 Surprise Bonus: You’ve unlocked an extra mini-tutorial — *Designing thumb-stopping social posts*. Keep pushing!"
            )
        except Exception:
            pass
    await update.message.reply_text(f"🎁 Bonus sent to {code}.")

# --------------------- PROGRESS (Student) ---------------------
async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = get_student_by_tg(update.effective_user.id)
    if not st:
        await update.message.reply_text("Please /start to register first.")
        return
    conn = db()
    cnt_all = conn.execute("SELECT COUNT(*) c FROM submissions WHERE student_code=?", (st["code"],)).fetchone()["c"]
    avg_row = conn.execute("SELECT AVG(grade) a FROM submissions WHERE student_code=? AND grade IS NOT NULL", (st["code"],)).fetchone()
    avg = round(avg_row["a"], 1) if avg_row and avg_row["a"] else "N/A"
    conn.close()
    await update.message.reply_text(
        f"📊 {st['full_name']} ({st['code']})\n"
        f"Submissions: {cnt_all}\nAverage Score: {avg}\nStatus: {st['status'].upper()}"
    )

# --------------------- CERTIFICATES & GRADUATION ---------------------
def student_completed_advanced(code: str) -> bool:
    """Simple rule: must submit lessons 5..12 (8 advanced days)."""
    conn = db()
    rows = conn.execute(
        "SELECT DISTINCT lesson_day FROM submissions WHERE student_code=? AND lesson_day BETWEEN 5 AND 12",
        (code,)
    ).fetchall()
    conn.close()
    days = {r["lesson_day"] for r in rows}
    return all(d in days for d in range(5, 13))

def make_certificate_image(name: str, code: str, out_path: str):
    if not PIL_OK:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"PHOTEX ACADEMY CERTIFICATE\n\nAwarded to: {name}\nCode: {code}\nDate: {datetime.now().date()}\n")
        return

    # Simple A4 certificate
    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    # Fallback fonts
    title_font = ImageFont.load_default()
    body_font = ImageFont.load_default()

    title = "PHOTEX MEDIA ACADEMY"
    sub = "Certificate of Completion"
    rec = f"Awarded to:\n{name}\n(Student Code: {code})"
    footer = f"Completed Advanced Training • {datetime.now().date()}"

    # Centered text
    draw.text((W/2 - 200, 200), title, fill=(10,10,10), font=title_font)
    draw.text((W/2 - 150, 260), sub, fill=(10,10,10), font=title_font)
    draw.multiline_text((200, 400), rec, fill=(20,20,20), font=body_font, spacing=10)
    draw.text((200, H-250), footer, fill=(20,20,20), font=body_font)

    img.save(out_path)

@must_be_admin
async def certify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Issue certificates to all who completed advanced."""
    conn = db()
    rows = conn.execute("SELECT full_name, code, tg_id FROM students WHERE graduated=0 AND status IN('advanced')").fetchall()
    conn.close()
    count = 0
    for r in rows:
        if not student_completed_advanced(r["code"]):
            continue
        cert_path = f"certificate_{r['code']}.{'png' if PIL_OK else 'txt'}"
        make_certificate_image(r["full_name"], r["code"], cert_path)
        try:
            await context.bot.send_message(GROUP_ID, f"🎖 {r['full_name']} ({r['code']}) has completed the program! Certificate issued.")
        except Exception:
            pass
        try:
            await context.bot.send_document(r["tg_id"], document=InputFile(cert_path), caption="🏆 Your Photex Certificate")
        except Exception:
            pass
        count += 1
    await update.message.reply_text(f"Certificates issued: {count}")

@must_be_admin
async def graduate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark graduates & remove them from the group."""
    conn = db()
    rows = conn.execute("SELECT id, tg_id, full_name, code FROM students WHERE graduated=0 AND status IN('advanced')").fetchall()
    removed = 0
    for r in rows:
        if not student_completed_advanced(r["code"]):
            continue
        # Mark graduated
        conn.execute("UPDATE students SET graduated=1, status='graduated' WHERE id=?", (r["id"],))
        # Try removing from group
        try:
            await context.bot.ban_chat_member(GROUP_ID, r["tg_id"])
            await context.bot.unban_chat_member(GROUP_ID, r["tg_id"])  # kick without ban
            removed += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Graduation complete. Removed from group: {removed}")

# --------------------- UPGRADE MESSAGE AFTER DAY 4 ---------------------
@must_be_trainer
async def post_upgrade_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "✅ *Free Basics Complete!*\n\n"
        "🚀 Advanced Training now open. Unlock:\n"
        "• Videography & Cinematic Shooting\n"
        "• Editing & Professional Color Grading\n"
        "• Advanced Graphics & Branding\n"
        "• Portrait & Product Photography\n"
        "• Marketing & Social Media Business\n"
        "🏆 Certificate of Completion\n\n"
        "Become a *Creative Professional* ready to design, shoot, edit, and market for real clients.\n"
        "To continue, make payment via MTN/Airtel/Bank and notify a trainer.\n"
        "Trainers will approve your access."
    )
    await context.bot.send_message(GROUP_ID, text, parse_mode=ParseMode.MARKDOWN)

# --------------------- HELP / INFO ---------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        textwrap.dedent("""
        🤝 Photex Media Service Team — commands

        For Students:
        /start  – Register (full name) and get your student code
        /progress – See your progress
        /submit <DAY>  – Submit your assignment (attach file!)

        For Trainers:
        /pin_today – Post & pin today’s prepared lesson
        /pin_custom <DAY> – Pin any lesson (1–12)
        /day_set <N>, /day_next – Control cohort day
        /pending <DAY> – Who hasn’t submitted
        /mark <CODE> <DAY> <SCORE> [feedback]
        /approve <CODE> – Approve into Advanced
        /pause <CODE>, /resume <CODE> – Manage attendance
        /report – Class report
        /star <CODE> <DAY> – Student of the Day
        /bonus <CODE> – Surprise bonus to student

        Admins only:
        /post_upgrade_notice – Announce Advanced after Day 4
        /certify – Issue certificates to completed students
        /graduate – Remove graduates from the group
        """)
    )

# --------------------- MAIN ---------------------
def main():
    init_db()
    preload_curriculum()

    # Default cohort day = 1 (first run)
    if not get_setting("cohort_day"):
        set_setting("cohort_day", "1")

    app = Application.builder().token(BOT_TOKEN).build()

    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_FULLNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, capture_fullname)]
        },
        fallbacks=[CommandHandler("cancel", cancel_reg)],
        allow_reentry=True
    )

    # Student handlers
    app.add_handler(reg_conv)
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("progress", progress))
    app.add_handler(CommandHandler("submit", submit))

    # Trainer & Admin handlers
    app.add_handler(CommandHandler("pin_today", pin_today))
    app.add_handler(CommandHandler("pin_custom", pin_custom))
    app.add_handler(CommandHandler("day_next", day_next))
    app.add_handler(CommandHandler("day_set", day_set))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("mark", mark))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("pause", pause_student))
    app.add_handler(CommandHandler("resume", resume_student))
    app.add_handler(CommandHandler("star", star))
    app.add_handler(CommandHandler("bonus", bonus))

    # Admin-only
    app.add_handler(CommandHandler("post_upgrade_notice", post_upgrade_notice))
    app.add_handler(CommandHandler("certify", certify))
    app.add_handler(CommandHandler("graduate", graduate))

    log.info("🤖 Photex Media Service Team is online.")
    app.run_polling()


if __name__ == "__main__":
    main()
