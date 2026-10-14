"""PROTOTYPE, throw away. Renders one weekly brief (reservation list only, no AI summary)
for one instrument from live booking API data, as an HTML file.

Question it answers: what does a real brief look like with real reservations?

Run (needs httpx + python-dotenv; the API key is read from bookig-api-json/booking-api-search/.env):
    python prototypes/brief-email/brief_prototype.py MIRA-STAN 2026-10-19 OUT_DIR

The output holds real personal data. Write it outside the repo.
"""

import html
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bookig-api-json" / "booking-api-search"))
from client import get_client  # noqa: E402

CANCELLED = 9
TENTATIVE = 1
WORK_START_HOUR = 7
WORK_END_HOUR = 19
NEW_USER_LIMIT = 3


def fetch_all(client, path, params):
    items, page = [], 1
    while True:
        r = client.get(path, params={**params, "limit": 50, "page": page})
        r.raise_for_status()
        chunk = r.json()
        if not chunk:
            return items
        items += chunk
        page += 1


def fetch(alias, monday):
    sunday_end = monday + timedelta(days=7)
    with get_client() as c:
        r = c.get("/api/equipment", params={"filters[]": f"ge_alias:eq:{alias}", "scope": "ge_ownerid"})
        r.raise_for_status()
        equipment = r.json()[0]
        week = [f"scheduledstart:gt:{monday}T00:00:00", f"scheduledstart:lt:{sunday_end}T00:00:00"]
        all_res = fetch_all(c, "/api/service-appointment", {"filters[]": week, "scope": "activity_parties"})
        ids = [x["id"] for x in all_res if any(p["partyid"]["id"] == equipment["id"] for p in x["activity_parties"])]
        reservations = []
        for rid in ids:
            r = c.get(f"/api/service-appointment/{rid}", params={"scope": "ge_realised_for_contactid,ownerid"})
            r.raise_for_status()
            reservations.append(r.json())
        rec_window = [f"new_start:gt:{monday - timedelta(days=1)}T00:00:00", f"new_start:lt:{sunday_end + timedelta(days=1)}T00:00:00"]
        recordings = fetch_all(c, "/api/recording", {"filters[]": rec_window, "scope": "ge_sessionid"})
        flags = user_flags(c, equipment, reservations)
    plans = {}
    for rec in recordings:
        sa = ((rec.get("ge_sessionid") or {}).get("ge_serviceappointmentid") or {}).get("id")
        if sa:
            plans[sa] = rec.get("new_plan_experimentu_new")
    return equipment, reservations, plans, flags


def user_flags(c, equipment, reservations):
    """Per reservation: which reservation number it is since the user's earliest certificate,
    and whether the user has a certificate valid on the reservation date."""
    guarantor = (equipment.get("ge_ownerid") or {}).get("id")
    flags = {}
    for cid in {contact_id(r) for r in reservations} - {None}:
        theirs = [r for r in reservations if contact_id(r) == cid]
        user = (theirs[0]["ge_realised_for_contactid"].get("ge_systemuserid") or {}).get("id")
        if user == guarantor:
            continue
        certs = []
        if user:
            r = c.get(f"/api/system-user/{user}/equipment/{equipment['id']}/certificates")
            r.raise_for_status()
            certs = r.json()
        since = min(((x.get("ge_platnostod") or "")[:19] for x in certs), default=None)
        starts, page = [], 1
        while since:
            # "ge_equipmentid" and "ge_realised_for_contactid" filters are not in the API spec, but work.
            r = c.get("/api/session", params={
                "limit": 20, "page": page, "order": "createdon:desc", "scope": "ge_serviceappointmentid",
                "filters[]": [f"ge_equipmentid:eq:{equipment['id']}", f"ge_realised_for_contactid:eq:{cid}"]})
            r.raise_for_status()
            chunk = [(x.get("ge_serviceappointmentid") or {}).get("scheduledstart") for x in r.json()]
            starts += [x for x in chunk if x]
            if not chunk or min(starts, default=since) < since or page >= 5:
                break
            page += 1
        for res in theirs:
            start = res["scheduledstart"]
            valid = [x for x in certs if (x.get("ge_platnostod") or "")[:19] <= start <= (x.get("ge_platnostdo") or "9999")[:19]]
            if valid:
                cert = None
            elif certs:
                cert = "expired " + max((x.get("ge_platnostdo") or "")[:10] for x in certs)
            else:
                cert = "missing"
            nth = sum(1 for x in starts if since <= x <= start) if since else None
            flags[res["id"]] = {"nth": nth, "cert": cert}
    return flags


def esc(text):
    return html.escape(text or "").replace("\n", "<br>")


def person(res):
    c = res.get("ge_realised_for_contactid") or {}
    name = f"{c.get('firstname') or ''} {c.get('lastname') or ''}".strip()
    return name or (res.get("ownerid") or {}).get("fullname") or "Unknown user"


def contact_id(res):
    return (res.get("ge_realised_for_contactid") or {}).get("id")


def time_range(res, sep="-"):
    start = datetime.fromisoformat(res["scheduledstart"])
    end = datetime.fromisoformat(res["scheduledend"])
    end_txt = f"{end:%a %H:%M}" if end.date() != start.date() else f"{end:%H:%M}"
    return f"{start:%H:%M}{sep}{end_txt}"


def short(res):
    start = datetime.fromisoformat(res["scheduledstart"])
    return f"{start:%a} {time_range(res)} {person(res)}"


def outside_hours(res):
    start = datetime.fromisoformat(res["scheduledstart"])
    end = datetime.fromisoformat(res["scheduledend"])
    late_end = end.hour > WORK_END_HOUR or (end.hour == WORK_END_HOUR and end.minute > 0) or end.date() > start.date()
    return start.weekday() >= 5 or start.hour < WORK_START_HOUR or late_end


def heads_ups(reservations, plans, flags):
    active = [r for r in reservations if r.get("statuscode") != CANCELLED]
    lines = []
    no_plan = [r for r in active if not (plans.get(r["id"]) or "").strip()]
    if no_plan:
        lines.append(f"<strong>{len(no_plan)} without a plan:</strong> " + ", ".join(esc(short(r)) for r in no_plan))
    late = [r for r in active if outside_hours(r)]
    if late:
        lines.append(f"<strong>{len(late)} outside working hours</strong> ({WORK_START_HOUR:02d}:00-{WORK_END_HOUR}:00, Mon-Fri): " + ", ".join(esc(short(r)) for r in late))
    new = [r for r in active if is_new(flags, r)]
    if new:
        lines.append(f"<strong>{len(new)} by a new user</strong> (first {NEW_USER_LIMIT} reservations since certification): "
                     + ", ".join(f"{esc(short(r))} (no. {flags[r['id']]['nth']})" for r in new))
    uncertified = [r for r in active if (flags.get(r["id"]) or {}).get("cert")]
    if uncertified:
        lines.append(f"<strong>{len(uncertified)} without a valid certificate:</strong> "
                     + ", ".join(f"{esc(short(r))} ({flags[r['id']]['cert']})" for r in uncertified))
    cancelled = [r for r in reservations if r.get("statuscode") == CANCELLED]
    tentative = [r for r in active if r.get("statuscode") == TENTATIVE]
    if cancelled or tentative:
        parts = ([f"{len(cancelled)} cancelled"] if cancelled else []) + ([f"{len(tentative)} tentative"] if tentative else [])
        lines.append(f"<strong>{' and '.join(parts)}:</strong> " + ", ".join(esc(short(r)) for r in cancelled + tentative))
    if not lines:
        return ""
    items = "".join(f'<li style="margin:4px 0;">{line}</li>' for line in lines)
    return f"""
      <div style="background:#eef4ff;border-left:4px solid #3b6fd8;border-radius:6px;padding:10px 14px;margin:0 0 16px;">
        <div style="font-weight:700;font-size:14px;margin-bottom:2px;">Heads-up</div>
        <ul style="margin:0;padding-left:18px;font-size:14px;">{items}</ul>
      </div>"""


def is_new(flags, res):
    nth = (flags.get(res["id"]) or {}).get("nth")
    return nth is not None and nth <= NEW_USER_LIMIT


def row_html(res, plan, flag):
    start = datetime.fromisoformat(res["scheduledstart"])
    end = datetime.fromisoformat(res["scheduledend"])
    minutes = round((end - start).total_seconds() / 60)
    length = f"{minutes} min" if minutes < 60 else f"{minutes / 60:.1f} h".replace(".0 h", " h")
    status = res.get("statuscode")
    note = (res.get("description") or "").strip()
    plan = (plan or "").strip()
    if note and plan and plan in note and len(note) - len(plan) < 15:
        note = ""
    badge = ""
    if status == CANCELLED:
        badge = '<span style="background:#fde2e1;color:#a4161a;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px;">CANCELLED</span> '
    elif status == TENTATIVE:
        badge = '<span style="background:#fff3cd;color:#7a5b00;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px;">TENTATIVE</span> '
    if flag and flag["nth"] is not None and flag["nth"] <= NEW_USER_LIMIT:
        badge += f'<span style="background:#e0ecff;color:#1e4fa8;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px;">NEW USER, NO. {flag["nth"]}</span> '
    if flag and flag["cert"]:
        badge += '<span style="background:#fde2e1;color:#a4161a;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px;">NO VALID CERTIFICATE</span> '
    strike = "text-decoration:line-through;color:#888;" if status == CANCELLED else ""
    plan_html = esc(plan) if plan else '<span style="color:#b45309;font-style:italic;">No plan given</span>'
    note_html = f'<div style="color:#555;font-size:13px;margin-top:4px;">Note: {esc(note)}</div>' if note else ""
    private = (res.get("ge_privatedescription") or "").strip()
    private_html = f'<div style="color:#555;font-size:13px;margin-top:4px;">Private note: {esc(private)}</div>' if private else ""
    return f"""
    <tr>
      <td style="width:96px;padding:10px 12px 10px 0;vertical-align:top;white-space:nowrap;font-variant-numeric:tabular-nums;{strike}">
        <strong>{time_range(res, sep="-<br>" if end.date() != start.date() else "-")}</strong><br><span style="color:#777;font-size:12px;">{length}</span>
      </td>
      <td style="padding:10px 0;vertical-align:top;">
        <div style="{strike}">{badge}<strong>{esc(person(res))}</strong></div>
        <div style="color:#777;font-size:12px;white-space:nowrap;{strike}">{esc(res.get('subject'))}</div>
        <div style="margin-top:4px;">{plan_html}</div>{note_html}{private_html}
      </td>
    </tr>"""


def render(equipment, reservations, plans, flags, monday):
    reservations = sorted(reservations, key=lambda r: r["scheduledstart"])
    active = [r for r in reservations if r.get("statuscode") != CANCELLED]
    total_h = sum((datetime.fromisoformat(r["scheduledend"]) - datetime.fromisoformat(r["scheduledstart"])).total_seconds() for r in active) / 3600
    no_plan = sum(1 for r in active if not (plans.get(r["id"]) or "").strip())
    cancelled = len(reservations) - len(active)
    users = len({person(r) for r in active})
    sunday = monday + timedelta(days=6)
    owner = (equipment.get("ge_ownerid") or {}).get("fullname") or ""
    days = ""
    for i in range(7):
        day = monday + timedelta(days=i)
        todays = [r for r in reservations if r["scheduledstart"][:10] == day.isoformat()]
        heading = f"{day:%A} {day.day}. {day.month}."
        if not todays:
            if i < 5:
                days += f'<div style="padding:10px 0;border-top:1px solid #e5e5e5;color:#999;"><strong>{heading}</strong> - no reservations</div>'
            continue
        rows = "".join(row_html(r, plans.get(r["id"]), flags.get(r["id"])) for r in todays)
        days += f"""
        <div style="border-top:1px solid #e5e5e5;padding-top:10px;margin-top:4px;">
          <div style="font-weight:700;font-size:15px;">{heading}</div>
          <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;">{rows}</table>
        </div>"""
    subject = f"Brief: {equipment.get('ge_alias')} - week {monday.day}.-{sunday.day}. {sunday.month}. {sunday.year}"
    return subject, f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(subject)}</title></head>
<body style="margin:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.4;">
  <div style="max-width:640px;margin:0 auto;padding:16px;">
    <div style="background:#fff7d6;border:1px dashed #c9a400;color:#5c4a00;font-size:12px;padding:6px 10px;margin-bottom:12px;border-radius:6px;">
      PROTOTYPE - real data pulled on {date.today():%d. %m. %Y}, not on Monday. Not sent to anyone.
    </div>
    <div style="font-size:12px;color:#666;">Subject: {esc(subject)}</div>
    <div style="background:#fff;border-radius:8px;padding:20px 20px 12px;margin-top:6px;">
      <div style="font-size:13px;color:#666;">Weekly brief for {esc(owner)}</div>
      <h1 style="font-size:20px;margin:4px 0 2px;">{esc(equipment.get('ge_nameen') or equipment.get('name'))}</h1>
      <div style="color:#666;font-size:13px;">{esc(equipment.get('ge_alias'))} - Monday {monday.day}. {monday.month}. to Sunday {sunday.day}. {sunday.month}. {sunday.year}</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin:16px 0;">
        <div style="background:#f4f4f5;border-radius:6px;padding:8px 12px;"><strong style="font-size:18px;">{len(active)}</strong> <span style="color:#555;font-size:13px;">reservations</span></div>
        <div style="background:#f4f4f5;border-radius:6px;padding:8px 12px;"><strong style="font-size:18px;">{total_h:.0f} h</strong> <span style="color:#555;font-size:13px;">booked</span></div>
        <div style="background:#f4f4f5;border-radius:6px;padding:8px 12px;"><strong style="font-size:18px;">{users}</strong> <span style="color:#555;font-size:13px;">users</span></div>
        <div style="background:#f4f4f5;border-radius:6px;padding:8px 12px;"><strong style="font-size:18px;">{no_plan}</strong> <span style="color:#555;font-size:13px;">without a plan</span></div>
        <div style="background:#f4f4f5;border-radius:6px;padding:8px 12px;"><strong style="font-size:18px;">{cancelled}</strong> <span style="color:#555;font-size:13px;">cancelled</span></div>
      </div>
      {heads_ups(reservations, plans, flags)}
      {days}
    </div>
    <div style="color:#777;font-size:12px;padding:12px 4px;">
      Built Monday 08:00. Check the booking system for later changes.<br>
      You get this because you subscribed as the guarantor of {esc(equipment.get('ge_alias'))}. <a href="#" style="color:#777;">Unsubscribe</a>
    </div>
  </div>
</body></html>"""


def main():
    alias, monday, out_dir = sys.argv[1], date.fromisoformat(sys.argv[2]), Path(sys.argv[3])
    equipment, reservations, plans, flags = fetch(alias, monday)
    subject, page = render(equipment, reservations, plans, flags, monday)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"brief-{alias}-{monday}.html").write_text(page, encoding="utf-8")
    (out_dir / f"brief-{alias}-{monday}.json").write_text(
        json.dumps({"equipment": equipment, "reservations": reservations, "plans": plans, "flags": flags}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(subject, "->", out_dir / f"brief-{alias}-{monday}.html")


if __name__ == "__main__":
    main()
