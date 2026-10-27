# ===== Quick Call Report (Zenput template 1761668) — GitHub Actions =====
# Reports YESTERDAY's calls (KSA calendar day 00:00-23:59):
#   1) area managers called, 2) those who did not attend,
#   3) area managers not called in the last 3 days (yesterday + 2 days before).
# PDF attached to the email, the three tables in the email body.
# If there were no calls yesterday, the email says: لا يوجد اتصالات خلال امس

import os, ast, html, asyncio, smtplib, requests, pytz
from datetime import datetime, timedelta
from collections import defaultdict
from email.message import EmailMessage
from email.utils import formataddr

# ---------------- CONFIG ----------------
API_TOKEN       = os.environ["ZENPUT_API_TOKEN"]
SENDER          = os.environ.get("SENDER_EMAIL", "aof.group.auto@gmail.com")
SENDER_PASS     = os.environ.get("SENDER_PASSWORD", "")
SENDER_NAME     = "Business Intelligence"
TO_LIST         = [x.strip() for x in os.environ.get("MAIL_TO", "a.alsalem@aofgroup.com,s.alharbi@aofgroup.com,a.omara@aofgroup.com,m.alhuaydar@aofgroup.com,o.salahaddin@aofgroup.com").split(",") if x.strip()]
CC_LIST         = [x.strip() for x in os.environ.get("MAIL_CC", "omar@aofgroup.com").split(",") if x.strip()]
SEND_EMAIL      = os.environ.get("SEND_EMAIL", "true").strip().lower() == "true"
REPORT_DATE_IN  = os.environ.get("REPORT_DATE", "").strip()   # "YYYY-MM-DD" or empty = yesterday

TEMPLATE_ID     = 1761668
BASE            = "https://www.zenput.com/api/v3/submissions/"
HEADERS         = {"X-API-TOKEN": API_TOKEN}
TZ              = pytz.timezone("Asia/Riyadh")
NOT_CALLED_DAYS = 3
OUT_HTML        = "quick_call_report.html"

Q_CALLED  = "اسم مدير المنطقة"
Q_PRESENT = "اسم مدير المنطقة الذي متواجد بالفرع"
Q_ABSENT  = "اسم مدير المنطقة الذي لم يحضر"
Q_ATTEND  = "هل تم الحضور من قبل المحددة اسمائهم؟"
Q_ALLIN   = "هل جميعهم متواجدين بالفروع؟"

# Active roster: Arabic form name -> English roster name
ROSTER = {
    "عدنان العربي":    "Adnan",
    "علي اسماعيل":     "ALI ESMAIL",
    "محمد الفضل":      "FADEL",
    "عمر الجوهري":     "omar",
    "محمد عماد":       "M.emad",
    "اسلام مصطفى":     "Islam",
    "نايف الزهراني":   "NAIF ALZAHRANI",
    "مقداد علي":       "Meqdad Ali",
    "ناريش جوشي":      "Naresh Joshe",
    "عبدالله الغانمي": "abdullah al ghanmi",
    "صلاح الدين":      "Salah Aldeen",
    "محمد سهيل":       "suhail",
    "سوزان بادول":     "Sujan",
}
# Names in the form that are not area managers — ignored everywhere
EXCLUDED = {"محمد غزالي", "سعد الشريف", "علام محمود", "محمد الحويدر", "اصيل عبدالله", "محمد مختار"}

PRESENT = "متواجد بالفرع"
ABSENT  = "لم يحضر"
UNKNOWN = "غير محدد"
NO_CALLS_MSG = "لا يوجد اتصالات خلال امس"


# ---------------- helpers ----------------
def parse_dt(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None

def as_list(v):
    if v in (None, "", []):
        return []
    if isinstance(v, str):
        try:
            v = ast.literal_eval(v)
        except Exception:
            v = [v]
    if not isinstance(v, list):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()]

def as_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    return str(v).strip().lower() in ("true", "1", "yes")

def esc(x):
    return html.escape(str(x))


# ---------------- dates ----------------
now = datetime.now(TZ)
report_date = (datetime.strptime(REPORT_DATE_IN, "%Y-%m-%d").date()
               if REPORT_DATE_IN else now.date() - timedelta(days=1))
win_start   = report_date - timedelta(days=NOT_CALLED_DAYS - 1)
fetch_floor = win_start - timedelta(days=30)   # history for "last called"
print(f"Now {now:%Y-%m-%d %H:%M} KSA | report day {report_date} | not-called window {win_start}..{report_date}")


# ---------------- fetch (client-side date filtering) ----------------
subs, start = [], 0
while True:
    r = requests.get(BASE, headers=HEADERS,
                     params={"form_template_id": TEMPLATE_ID, "limit": 100, "start": start}, timeout=60)
    r.raise_for_status()
    batch = r.json().get("data", [])
    if not batch:
        break
    subs.extend(batch)
    dts = [parse_dt((s.get("smetadata") or {}).get("date_submitted")) for s in batch]
    dts = [d for d in dts if d]
    if dts and min(dts).date() < fetch_floor:
        break
    start += 100
print(f"Fetched {len(subs)} submissions")


# ---------------- parse calls: one event per (submission, manager) ----------------
events = []
for s in subs:
    m  = s.get("smetadata") or {}
    dt = parse_dt(m.get("date_submitted"))
    if not dt or dt.date() > report_date:
        continue
    ans = {a.get("title"): a.get("value") for a in s.get("answers", [])}
    called  = as_list(ans.get(Q_CALLED))
    present = set(as_list(ans.get(Q_PRESENT)))
    absent  = set(as_list(ans.get(Q_ABSENT)))
    attend  = as_bool(ans.get(Q_ATTEND))
    allin   = as_bool(ans.get(Q_ALLIN))
    caller  = (m.get("created_by") or {}).get("display_name", "")

    for name in called:
        if name in EXCLUDED:
            continue
        if name in absent:
            status = ABSENT
        elif name in present:
            status = PRESENT
        elif attend is True and allin is True:
            status = PRESENT
        elif attend is False and not absent:
            status = ABSENT          # flagged "no" but list left empty
        else:
            status = UNKNOWN
        events.append({"name": name, "dt": dt, "day": dt.date(), "status": status, "caller": caller})

    for name in absent - set(called) - EXCLUDED:
        events.append({"name": name, "dt": dt, "day": dt.date(), "status": ABSENT, "caller": caller})

day_ev = [e for e in events if e["day"] == report_date]
print(f"Events total {len(events)} | on report day {len(day_ev)}")


# ---------------- tables ----------------
by_mgr = defaultdict(list)
for e in day_ev:
    by_mgr[e["name"]].append(e)

called_rows = []
for name, evs in by_mgr.items():
    evs.sort(key=lambda e: e["dt"])
    last = evs[-1]
    called_rows.append({
        "name": name, "en": ROSTER.get(name, "—"), "calls": len(evs),
        "last": last["dt"], "status": last["status"], "caller": last["caller"],
        "ever_absent": any(e["status"] == ABSENT for e in evs),
    })
called_rows.sort(key=lambda r: r["last"], reverse=True)
absent_rows = [r for r in called_rows if r["ever_absent"]]

last_called = {}
for e in events:
    if e["name"] not in last_called or e["dt"] > last_called[e["name"]]:
        last_called[e["name"]] = e["dt"]
called_in_window = {e["name"] for e in events if win_start <= e["day"] <= report_date}

not_called_rows = []
for name, en in ROSTER.items():
    if name in called_in_window:
        continue
    lc = last_called.get(name)
    not_called_rows.append({"name": name, "en": en, "last": lc,
                            "days": (report_date - lc.date()).days if lc else None})
not_called_rows.sort(key=lambda r: (r["days"] is not None, -(r["days"] or 0)))
present_cnt = sum(1 for r in called_rows if r["status"] == PRESENT)

print(f"Called {len(called_rows)} | present {present_cnt} | not attended {len(absent_rows)} | not called {NOT_CALLED_DAYS}d {len(not_called_rows)}")
for r in called_rows:
    print(f"  {r['name']} ({r['en']}) | {r['calls']} call(s) | last {r['last']:%H:%M} | {r['status']}")


# ---------------- HTML (inline styles — works in both PDF and email) ----------------
TH = "background:#1e293b;color:#ffffff;padding:8px;text-align:right;font-weight:600;font-size:14px;"
TD = "padding:8px;border-bottom:1px solid #e5e9ef;text-align:right;font-size:14px;"
BADGE = {
    PRESENT: "background:#dcfce7;color:#166534;",
    ABSENT:  "background:#fee2e2;color:#991b1b;",
    UNKNOWN: "background:#e5e7eb;color:#374151;",
}

def badge(status):
    st = BADGE.get(status, BADGE[UNKNOWN])
    return f'<span style="{st}padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;">{esc(status)}</span>'

def table(headers, rows, empty):
    if not rows:
        return f'<div style="color:#6b7785;padding:10px;text-align:center;font-size:14px;">{esc(empty)}</div>'
    th = "".join(f'<th style="{TH}">{esc(h)}</th>' for h in headers)
    body = ""
    for i, row in enumerate(rows):
        bg = "background:#f8fafc;" if i % 2 else ""
        body += "<tr>" + "".join(f'<td style="{TD}{bg}">{c}</td>' for c in row) + "</tr>"
    return (f'<table dir="rtl" style="width:100%;border-collapse:collapse;" cellpadding="0" cellspacing="0">'
            f"<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")

t_called = table(
    ["#", "مدير المنطقة", "Area Manager", "عدد الاتصالات", "آخر اتصال", "الحالة", "المتصل"],
    [[i + 1, esc(r["name"]), esc(r["en"]), r["calls"], r["last"].strftime("%H:%M"),
      badge(r["status"]), esc(r["caller"])] for i, r in enumerate(called_rows)],
    "لم يتم الاتصال بأي مدير منطقة")
t_absent = table(
    ["#", "مدير المنطقة", "Area Manager", "وقت الاتصال", "المتصل"],
    [[i + 1, esc(r["name"]), esc(r["en"]), r["last"].strftime("%H:%M"), esc(r["caller"])]
     for i, r in enumerate(absent_rows)],
    "جميع من تم الاتصال بهم متواجدون")
t_not_called = table(
    ["#", "مدير المنطقة", "Area Manager", "آخر اتصال", "منذ (يوم)"],
    [[i + 1, esc(r["name"]), esc(r["en"]),
      r["last"].strftime("%Y-%m-%d %H:%M") if r["last"] else "لا يوجد",
      r["days"] if r["days"] is not None else "—"] for i, r in enumerate(not_called_rows)],
    f"تم الاتصال بجميع مدراء المناطق خلال آخر {NOT_CALLED_DAYS} أيام")

SEC = "background:#ffffff;border-radius:10px;padding:16px;margin-bottom:18px;border:1px solid #e5e9ef;"
H2  = "font-size:17px;margin:0 0 12px 0;color:#1f2933;"
sections = (
    f'<div style="{SEC}"><h2 style="{H2}">مدراء المناطق الذين تم الاتصال بهم</h2>{t_called}</div>'
    f'<div style="{SEC}"><h2 style="{H2}">مدراء المناطق الذين لم يحضروا</h2>{t_absent}</div>'
    f'<div style="{SEC}"><h2 style="{H2}">مدراء المناطق الذين لم يتم الاتصال بهم آخر {NOT_CALLED_DAYS} أيام '
    f'({win_start:%Y-%m-%d} — {report_date:%Y-%m-%d})</h2>{t_not_called}</div>'
)

def card(value, label, color):
    return (f'<div style="background:#fff;border-radius:10px;padding:14px 16px;border:1px solid #e5e9ef;'
            f'border-top:4px solid {color};"><div style="font-size:28px;font-weight:700;">{value}</div>'
            f'<div style="color:#6b7785;font-size:13px;">{esc(label)}</div></div>')

cards = (
    card(len(ROSTER), "إجمالي مدراء المناطق", "#1d4ed8")
    + card(len(called_rows), "تم الاتصال بهم", "#1d4ed8")
    + card(present_cnt, "متواجدون بالفرع", "#15803d")
    + card(len(absent_rows), "لم يحضروا", "#b91c1c")
    + card(len(not_called_rows), f"لم يتم الاتصال بهم آخر {NOT_CALLED_DAYS} أيام", "#c2410c")
)

TITLE = "تقرير الاتصال السريع بمدراء المناطق"
page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">
<style>body{{font-family:Cairo,Tahoma,sans-serif;background:#f4f6f9;color:#1f2933;margin:0;padding:24px}}
.wrap{{max-width:1100px;margin:auto}}
.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:22px}}</style>
</head><body><div class="wrap">
<h1 style="margin:0;font-size:24px;">{TITLE}</h1>
<div style="color:#6b7785;margin:4px 0 20px;">Quick Call Report — {report_date:%Y-%m-%d} | تاريخ الإصدار {now:%Y-%m-%d %H:%M}</div>
<div class="cards">{cards}</div>
{sections}
</div></body></html>"""


# ---------------- PDF: Playwright screenshot -> fpdf2 ----------------
async def render_pdf(html_path, pdf_path):
    from playwright.async_api import async_playwright
    from PIL import Image
    from fpdf import FPDF
    Image.MAX_IMAGE_PIXELS = None
    png_path = pdf_path.replace(".pdf", ".png")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        pg = await browser.new_page(viewport={"width": 1150, "height": 800}, device_scale_factor=2)
        await pg.goto("file://" + os.path.abspath(html_path), wait_until="networkidle")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=png_path, full_page=True)
        await browser.close()
    img = Image.open(png_path)
    w_pt, h_pt = img.width * 72 / 192, img.height * 72 / 192   # scale 2 => 192 dpi
    pdf = FPDF(unit="pt", format=(w_pt, h_pt))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.image(png_path, x=0, y=0, w=w_pt, h=h_pt)
    pdf.output(pdf_path)
    return pdf_path


# ---------------- email ----------------
def send_mail(subject, html_body, attachment=None):
    if not SEND_EMAIL:
        print("SEND_EMAIL is false — email skipped")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, SENDER))
    msg["To"] = ", ".join(TO_LIST)
    if CC_LIST:
        msg["Cc"] = ", ".join(CC_LIST)
    msg.set_content(f"{subject}\n\nيرجى عرض الرسالة بصيغة HTML.")
    msg.add_alternative(html_body, subtype="html")
    if attachment:
        with open(attachment, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                               filename=os.path.basename(attachment))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SENDER, SENDER_PASS)
        s.send_message(msg, to_addrs=TO_LIST + CC_LIST)
    print(f"Email sent to {TO_LIST} cc {CC_LIST}")

def email_wrap(inner):
    return (f'<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif;background:#f4f6f9;padding:20px;color:#1f2933;">'
            f'<div style="max-width:900px;margin:auto;">{inner}'
            f'<div style="color:#6b7785;font-size:12px;margin-top:10px;">Business Intelligence — AOF Group</div>'
            f"</div></div>")

subject = f"{TITLE} - {report_date:%Y-%m-%d}"

if not day_ev:
    print(NO_CALLS_MSG)
    body = email_wrap(
        f'<h2 style="margin:0 0 6px 0;font-size:20px;">{TITLE}</h2>'
        f'<div style="color:#6b7785;margin-bottom:16px;">{report_date:%Y-%m-%d}</div>'
        f'<div style="{SEC}font-size:16px;font-weight:600;text-align:center;">{NO_CALLS_MSG}</div>'
    )
    send_mail(subject, body)
else:
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    pdf_file = asyncio.run(render_pdf(OUT_HTML, f"quick_call_report_{report_date:%Y-%m-%d}.pdf"))
    print(f"PDF saved: {pdf_file}")
    summary = (f'<div style="margin-bottom:16px;font-size:14px;">'
               f'تم الاتصال بـ <b>{len(called_rows)}</b> من مدراء المناطق، '
               f'المتواجدون بالفرع <b>{present_cnt}</b>، لم يحضروا <b>{len(absent_rows)}</b>، '
               f'ولم يتم الاتصال بـ <b>{len(not_called_rows)}</b> خلال آخر {NOT_CALLED_DAYS} أيام. '
               f'التقرير الكامل مرفق بصيغة PDF.</div>')
    body = email_wrap(
        f'<h2 style="margin:0 0 6px 0;font-size:20px;">{TITLE}</h2>'
        f'<div style="color:#6b7785;margin-bottom:12px;">{report_date:%Y-%m-%d}</div>'
        f"{summary}{sections}"
    )
    send_mail(subject, body, pdf_file)
