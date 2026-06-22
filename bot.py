#!/usr/bin/env python3
"""
RideNow Cabriolet Tracker — Telegram bot (button-driven, cabrio-only).

Watches a public RideNow / CarTrek car feed for the BMW 4 Cabrio (only ~2 on
Cyprus, visible only when free) and alerts when one enters / leaves a radius
around a chosen point. Button-driven UI; the menu message auto-refreshes so it
always looks alive. Stdlib only (urllib + sqlite3 + threading).

EDUCATIONAL / RESEARCH USE ONLY — see README. No token is stored here; it is
read from the BOT_TOKEN environment variable (or a local .env file).
"""
import os, sys, json, time, math, re, sqlite3, threading, traceback
import urllib.request, urllib.parse, urllib.error, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
def load_env():
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for ln in open(p):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
load_env()
TOKEN    = os.environ.get("BOT_TOKEN", "").strip()
POLL_SEC = int(os.environ.get("POLL_SEC", "90"))
COOLDOWN = int(os.environ.get("COOLDOWN_MIN", "30")) * 60
DB_PATH  = os.path.join(HERE, "state.db")
API      = f"https://api.telegram.org/bot{TOKEN}"
FEED_URL = "https://ridenow3.ct.ms/api/v2/cars"
FEED_UA  = "okhttp/4.12.0"
CABRIO_CLASS_ID = "b72968f1-e6dc-4a51-adee-b27000ad59a0"
CY_TZ    = datetime.timezone(datetime.timedelta(hours=3))   # Cyprus EEST

CITIES = {
    "Limassol":    (34.685318, 33.030684, "Limassol"),
    "Nicosia":     (35.172867, 33.354177, "Nicosia"),
    "Larnaca":     (34.909679, 33.629847, "Larnaca"),
    "Paphos":      (34.778392, 32.427002, "Paphos"),
    "AyiaNapa":    (34.990190, 33.998705, "Ayia Napa"),
    "PanoPlatres": (34.888471, 32.864568, "Pano Platres"),
}
RADII = [5, 10, 20, 30, 50, 100]

# ------------------------------------------------------------------ util
def log(*a): print(f"[{datetime.datetime.now(CY_TZ):%H:%M:%S}]", *a, flush=True)
def now_hms(): return datetime.datetime.now(CY_TZ).strftime("%H:%M:%S")
def haversine_km(a, b, c, d):
    R = 6371.0; p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c-a); dl = math.radians(d-b)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(h))
def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def maps_link(lat, lon): return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
def app_link(car_id): return f"https://app.cartrek.online/distrib/?targetName=ridenow&carId={car_id}"
def fmt_radius(r): r=float(r); return f"{int(round(r*1000))} m" if r<1 else f"{r:g} km"
def parse_radius(t):
    s=(t or "").strip().lower().replace(",",".").replace(" ","")
    try:
        if s.endswith("km"): return float(s[:-2])
        if s.endswith("k"): return float(s[:-1])
        if s.endswith("m"): return float(s[:-1])/1000.0
        return float(s)
    except Exception: return None
def parse_coords(t):
    m=re.findall(r"[-+]?\d{1,3}(?:\.\d+)?",(t or "").replace(","," "))
    if len(m)>=2:
        lat,lon=float(m[0]),float(m[1])
        if -90<=lat<=90 and -180<=lon<=180: return lat,lon
    return None
def geocode(addr):
    for extra in ({"countrycodes":"cy"},{}):
        try:
            p={"q":addr,"format":"json","limit":1}; p.update(extra)
            req=urllib.request.Request("https://nominatim.openstreetmap.org/search?"+urllib.parse.urlencode(p),
                                       headers={"User-Agent":"ridenow-cabrio-bot/1.0"})
            with urllib.request.urlopen(req,timeout=15) as r: arr=json.load(r)
            if arr: it=arr[0]; return float(it["lat"]),float(it["lon"]),it.get("display_name",addr)[:55]
        except Exception as e: log("GEOCODE",repr(e))
    return None

# ------------------------------------------------------------------ telegram
def tg(method, params=None, timeout=35):
    data=urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{API}/{method}",data=data),timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try: body=json.load(e)
        except Exception: body={"description":str(e)}
        if "not modified" not in str(body).lower(): log("TG",method,e.code,str(body)[:140])
        return {"ok":False,**(body if isinstance(body,dict) else {})}
    except Exception as e:
        log("TG",method,repr(e)); return {"ok":False,"description":repr(e)}
def kb(rows): return {"inline_keyboard":[[{"text":t,"callback_data":d} for t,d in r] for r in rows]}
def btn(text,data): return {"text":text,"callback_data":data}
def burl(text,url): return {"text":text,"url":url}
def send(chat,text,markup=None):
    p={"chat_id":chat,"text":text,"parse_mode":"HTML","disable_web_page_preview":"true"}
    if markup is not None: p["reply_markup"]=json.dumps(markup)
    return tg("sendMessage",p).get("result",{}).get("message_id")
def edit(chat,mid,text,markup=None):
    p={"chat_id":chat,"message_id":mid,"text":text,"parse_mode":"HTML","disable_web_page_preview":"true"}
    if markup is not None: p["reply_markup"]=json.dumps(markup)
    return tg("editMessageText",p)
def answer_cb(cid,text=None,alert=False):
    p={"callback_query_id":cid}
    if text: p["text"]=text
    if alert: p["show_alert"]="true"
    tg("answerCallbackQuery",p)

# ------------------------------------------------------------------ db
_lock=threading.Lock()
_conn=sqlite3.connect(DB_PATH,check_same_thread=False); _conn.row_factory=sqlite3.Row
def q(sql,args=(),many=False,commit=False):
    with _lock:
        cur=_conn.execute(sql,args)
        if commit: _conn.commit()
        return cur.fetchall() if many else cur.fetchone()
def init_db():
    with _lock:
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(chat_id INTEGER PRIMARY KEY, city TEXT, lat REAL, lon REAL,
            radius REAL DEFAULT 30, paused INTEGER DEFAULT 0, menu_mid INTEGER,
            view TEXT DEFAULT 'main', created TEXT);
        CREATE TABLE IF NOT EXISTS cabrio_state(chat_id INTEGER, car_id TEXT,
            in_radius INTEGER, last_alert_ts INTEGER, last_alert_kind TEXT,
            PRIMARY KEY(chat_id, car_id));
        CREATE TABLE IF NOT EXISTS pending(chat_id INTEGER PRIMARY KEY, action TEXT);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        """); _conn.commit()
def meta_get(k,default=None):
    r=q("SELECT v FROM meta WHERE k=?",(k,)); return r["v"] if r else default
def meta_set(k,v): q("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",(k,str(v)),commit=True)
def user(chat):
    u=q("SELECT * FROM users WHERE chat_id=?",(chat,))
    if not u:
        q("INSERT INTO users(chat_id,radius,view,created) VALUES(?,?,?,?)",
          (chat,30,'main',datetime.datetime.now(CY_TZ).isoformat()),commit=True)
        u=q("SELECT * FROM users WHERE chat_id=?",(chat,))
    return u
def set_view(chat,v): q("UPDATE users SET view=? WHERE chat_id=?",(v,chat),commit=True)
def set_center(chat,label,lat,lon): q("UPDATE users SET city=?,lat=?,lon=? WHERE chat_id=?",(label,lat,lon,chat),commit=True)

# ------------------------------------------------------------------ feed
def fetch_cabrios():
    req=urllib.request.Request(FEED_URL,headers={"User-Agent":FEED_UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r: d=json.load(r)
    sch,models,cars=d["carSchema"],d["carModels"],d["cars"]; i={n:k for k,n in enumerate(sch)}
    out=[]
    for row in cars:
        cm=row[i["carModel"]]; m=models[cm] if cm<len(models) else None
        if m and m.get("modelClassId")==CABRIO_CLASS_ID:
            out.append({"id":row[i["id"]],"model":f"{m['brand']} {m['model']}","plate":row[i["regNumber"]],
                        "lat":row[i["lat"]],"lon":row[i["lon"]],"fuel":row[i["fuelLevel"]]})
    return out
def center_name(u): return CITIES.get(u["city"],(0,0,u["city"]))[2] if u["city"] else "not set"
def in_radius(u,cabs):
    if not u["city"]: return []
    r=[(haversine_km(u["lat"],u["lon"],c["lat"],c["lon"]),c) for c in cabs]
    return sorted([(d,c) for d,c in r if d<=u["radius"]])

# ------------------------------------------------------------------ views
def v_main(chat, cabs=None):
    u=user(chat)
    if cabs is None:
        try: cabs=fetch_cabrios()
        except Exception: cabs=None
    has_pt=bool(u["city"]); on=not u["paused"]
    if not has_pt:   status="⚠️ <b>Set a location</b> to start tracking"
    elif on:         status="🟢 <b>Tracking</b> — I'll ping you when a cabrio is near"
    else:            status="⏸ <b>Paused</b>"
    L=["🏎️ <b>Cabriolet Tracker</b>","",
       f"📍 Location: <b>{esc(center_name(u))}</b>",
       f"📏 Radius: <b>{fmt_radius(u['radius'])}</b>",
       status,""]
    inr=[]
    if cabs is None:
        L.append("⚠️ feed temporarily unavailable")
    else:
        inr=in_radius(u,cabs)
        for d,c in inr:
            L.append(f"🎉 <b>{esc(c['model'])}</b> <code>{esc(c['plate'])}</code> — {d:.1f} km · ⛽{c['fuel']}%")
        L.append(f"🏎️ Cabriolets free on Cyprus now: <b>{len(cabs)}</b>")
    L.append(f"🕑 Updated {now_hms()} · auto every {POLL_SEC}s")
    rows=[]
    for d,c in inr:                       # tap straight into the app for a nearby cabrio
        rows.append([burl(f"🚗 {c['plate']} → open in app", app_link(c["id"]))])
    rows.append([btn("📍 Location","go:loc"), btn("📏 Radius","go:rad")])
    rows.append([btn("🔍 Check now","go:check")])
    if has_pt:
        rows.append([btn("⏸ Pause","act:off") if on else btn("▶️ Resume tracking","act:on")])
    return "\n".join(L), {"inline_keyboard":rows}

def v_loc(chat):
    rows,cur=[],[]
    for k,(_,_,disp) in CITIES.items():
        cur.append((disp,f"city:{k}"))
        if len(cur)==2: rows.append(cur); cur=[]
    if cur: rows.append(cur)
    rows.append([("📍 Send my location","loc:send")])
    rows.append([("✏️ Coordinates / address","loc:text")])
    rows.append([("‹ Back","go:main")])
    return "📍 <b>Location</b> — the centre of your radius.\nPick a city or send your own:", kb(rows)

def v_rad(chat):
    u=user(chat)
    rows=[[(f"{r} km",f"rad:{r}") for r in RADII[:3]],
          [(f"{r} km",f"rad:{r}") for r in RADII[3:]],
          [("✏️ Custom (m / km)","rad:custom")],
          [("‹ Back","go:main")]]
    return f"📏 <b>Radius</b>\nNow: <b>{fmt_radius(u['radius'])}</b>\nPick one — back to the menu:", kb(rows)

def v_onboard(chat):
    text=("👋 <b>RideNow Cabriolet Tracker</b>\n\n"
          "I watch for the BMW 4 Cabrio (only ~2 on Cyprus) and ping you the moment one shows up nearby.\n\n"
          "<b>Step 1.</b> Where should the tracking centre be?\n"
          "Pick a city or send your own location 👇")
    _, markup = v_loc(chat)
    return text, markup

VIEWS={"main":v_main,"loc":v_loc,"rad":v_rad}
def show(chat,mid,name):
    set_view(chat,name); text,markup=VIEWS[name](chat); edit(chat,mid,text,markup)
def open_menu(chat):
    set_view(chat,'main'); text,markup=v_main(chat); mid=send(chat,text,markup)
    q("UPDATE users SET menu_mid=? WHERE chat_id=?",(mid,chat),commit=True); return mid
def start_or_menu(chat):
    u=user(chat)
    if not u["city"]:
        set_view(chat,'loc'); text,markup=v_onboard(chat)
        mid=send(chat,text,markup); q("UPDATE users SET menu_mid=? WHERE chat_id=?",(mid,chat),commit=True); return mid
    return open_menu(chat)

LOC_REPLY={"keyboard":[[{"text":"📍 Send my location","request_location":True}],[{"text":"✖️ Cancel"}]],
           "resize_keyboard":True,"one_time_keyboard":True}
REMOVE_KB={"remove_keyboard":True}

# ------------------------------------------------------------------ handlers
def on_message(msg, edited=False):
    chat=msg["chat"]["id"]; user(chat)
    if msg.get("location"):
        loc=msg["location"]; set_center(chat,"📍 my location",loc["latitude"],loc["longitude"])
        if edited: return
        q("DELETE FROM pending WHERE chat_id=?",(chat,),commit=True)
        send(chat,f"✅ Location set: <code>{loc['latitude']:.4f}, {loc['longitude']:.4f}</code>",REMOVE_KB)
        open_menu(chat); return
    text=(msg.get("text") or "").strip()
    if text=="✖️ Cancel":
        q("DELETE FROM pending WHERE chat_id=?",(chat,),commit=True); send(chat,"OK.",REMOVE_KB); open_menu(chat); return
    pend=q("SELECT action FROM pending WHERE chat_id=?",(chat,))
    if pend and text and not text.startswith("/"):
        act=pend["action"]; q("DELETE FROM pending WHERE chat_id=?",(chat,),commit=True)
        if act=="radius":
            r=parse_radius(text)
            if r and 0.02<=r<=500: q("UPDATE users SET radius=? WHERE chat_id=?",(r,chat),commit=True); send(chat,f"✅ Radius: <b>{fmt_radius(r)}</b>")
            else: send(chat,"Didn't get that. Examples: <code>100m</code>, <code>2km</code>, <code>30</code>")
            open_menu(chat); return
        if act=="place":
            c=parse_coords(text)
            if c: set_center(chat,"📍 coordinates",c[0],c[1]); send(chat,f"✅ Location: <code>{c[0]:.4f}, {c[1]:.4f}</code>")
            else:
                g=geocode(text)
                if g: set_center(chat,f"📍 {g[2]}",g[0],g[1]); send(chat,f"✅ Location: <b>{esc(g[2])}</b>")
                else: send(chat,"Couldn't find it. Send «lat, lon» or a more precise address.")
            open_menu(chat); return
    if text and not text.startswith("/"):
        c=parse_coords(text)
        if c: set_center(chat,"📍 coordinates",c[0],c[1]); send(chat,f"✅ Location: <code>{c[0]:.4f}, {c[1]:.4f}</code>"); open_menu(chat); return
    start_or_menu(chat)   # /start, /menu or anything else -> onboarding if no centre, else menu

def on_callback(cb):
    chat=cb["message"]["chat"]["id"]; mid=cb["message"]["message_id"]; data=cb.get("data",""); cid=cb["id"]
    user(chat); q("UPDATE users SET menu_mid=? WHERE chat_id=?",(mid,chat),commit=True)
    try:
        if data=="go:main":
            answer_cb(cid); show(chat,mid,"main"); return
        if data=="go:check":
            try:
                cabs=fetch_cabrios(); u=user(chat); inr=in_radius(u,cabs)
                if inr:
                    d,c=inr[0]; answer_cb(cid,f"🎉 {c['plate']} nearby — {d:.1f} km!",alert=True)
                elif not u["city"]:
                    answer_cb(cid,f"🏎️ {len(cabs)} free. Set a location to catch nearby ones.",alert=True)
                else:
                    answer_cb(cid,f"🏎️ {len(cabs)} free now. None in radius yet — watching.")
                set_view(chat,"main"); text,markup=v_main(chat,cabs); edit(chat,mid,text,markup)
            except Exception:
                answer_cb(cid,"⚠️ feed unavailable, try again",alert=True)
            return
        if data.startswith("go:"):
            answer_cb(cid); show(chat,mid,data[3:]); return
        if data.startswith("city:"):
            k=data[5:]; t=None
            if k in CITIES:
                lat,lon,disp=CITIES[k]; set_center(chat,k,lat,lon); t=f"📍 {disp}"
            answer_cb(cid,t); show(chat,mid,"main"); return
        if data=="loc:send":
            q("INSERT OR REPLACE INTO pending(chat_id,action) VALUES(?, 'place')",(chat,),commit=True)
            answer_cb(cid); send(chat,"📍 Tap the button below to share your location:",LOC_REPLY); return
        if data=="loc:text":
            q("INSERT OR REPLACE INTO pending(chat_id,action) VALUES(?, 'place')",(chat,),commit=True)
            answer_cb(cid); send(chat,"✏️ Send coordinates (<code>34.70, 33.02</code>) or an address:"); return
        if data=="rad:custom":
            q("INSERT OR REPLACE INTO pending(chat_id,action) VALUES(?, 'radius')",(chat,),commit=True)
            answer_cb(cid); send(chat,"✏️ Enter radius: <code>100m</code>, <code>0.5</code>, <code>2km</code>:"); return
        if data.startswith("rad:"):
            r=float(data[4:]); q("UPDATE users SET radius=? WHERE chat_id=?",(r,chat),commit=True)
            answer_cb(cid,f"📏 Radius {fmt_radius(r)} ✓"); show(chat,mid,"main"); return
        if data=="act:off":
            q("UPDATE users SET paused=1 WHERE chat_id=?",(chat,),commit=True)
            answer_cb(cid,"⏸ Paused"); show(chat,mid,"main"); return
        if data=="act:on":
            q("UPDATE users SET paused=0 WHERE chat_id=?",(chat,),commit=True)
            answer_cb(cid,"🟢 Tracking on"); show(chat,mid,"main"); return
        answer_cb(cid)
    except Exception as e:
        answer_cb(cid,"⚠️ error, try again"); log("CB",data,repr(e)); traceback.print_exc()

# ------------------------------------------------------------------ tracking
def alert(u,car,dist,kind,cid):
    chat=u["chat_id"]
    row=q("SELECT last_alert_ts,last_alert_kind FROM cabrio_state WHERE chat_id=? AND car_id=?",(chat,cid))
    nowt=int(time.time())
    if row and row["last_alert_kind"]==kind and row["last_alert_ts"] and nowt-row["last_alert_ts"]<COOLDOWN: return
    if kind=="enter" and car:
        txt=(f"🏎️🎉 <b>Cabriolet nearby!</b>\n<b>{esc(car['model'])}</b> <code>{esc(car['plate'])}</code>\n"
             f"📍 {dist:.1f} km from your point ({esc(center_name(u))}) · ⛽ {car['fuel']}%")
        send(chat,txt,{"inline_keyboard":[
            [burl("🚗 Open in app", app_link(car["id"]))],
            [burl("🗺 Open map", maps_link(car["lat"],car["lon"]))]]})
    else:
        send(chat,f"🏁 Cabriolet <code>{esc(car['plate']) if car else ''}</code> left the radius.")
    q("""INSERT INTO cabrio_state(chat_id,car_id,in_radius,last_alert_ts,last_alert_kind) VALUES(?,?,?,?,?)
         ON CONFLICT(chat_id,car_id) DO UPDATE SET in_radius=excluded.in_radius,
         last_alert_ts=excluded.last_alert_ts,last_alert_kind=excluded.last_alert_kind""",
      (chat,cid,1 if kind=="enter" else 0,nowt,kind),commit=True)

def cycle():
    cabs=fetch_cabrios(); meta_set("last_poll",time.time())
    for u in q("SELECT * FROM users WHERE paused=0 AND city IS NOT NULL",many=True):
        present={c["id"]:(c,d) for d,c in in_radius(u,cabs)}
        prev={r["car_id"] for r in q("SELECT car_id FROM cabrio_state WHERE chat_id=? AND in_radius=1",(u["chat_id"],),many=True)}
        for k in set(present)-prev:
            c,d=present[k]; alert(u,c,d,"enter",k)
        for k in prev-set(present):
            c=next((x for x in cabs if x["id"]==k),None); alert(u,c,None,"leave",k)
            q("UPDATE cabrio_state SET in_radius=0 WHERE chat_id=? AND car_id=?",(u["chat_id"],k),commit=True)
    for u in q("SELECT * FROM users WHERE menu_mid IS NOT NULL AND view='main'",many=True):
        try:
            text,markup=v_main(u["chat_id"],cabs); edit(u["chat_id"],u["menu_mid"],text,markup)
        except Exception: pass

def tracking_loop():
    while True:
        try: cycle()
        except Exception as e: log("TRACK",repr(e))
        time.sleep(POLL_SEC)

# ------------------------------------------------------------------ main
def update_loop():
    tg("deleteWebhook",{"drop_pending_updates":"false"})
    tg("setMyCommands",{"commands":json.dumps([{"command":"menu","description":"Open menu"}])})
    log("update loop started"); offset=None
    while True:
        try:
            p={"timeout":25,"allowed_updates":json.dumps(["message","edited_message","callback_query"])}
            if offset is not None: p["offset"]=offset
            res=tg("getUpdates",p,timeout=35)
            if not res.get("ok"): time.sleep(3); continue
            for upd in res["result"]:
                offset=upd["update_id"]+1
                try:
                    if "message" in upd: on_message(upd["message"])
                    elif "edited_message" in upd and upd["edited_message"].get("location"): on_message(upd["edited_message"],edited=True)
                    elif "callback_query" in upd: on_callback(upd["callback_query"])
                except Exception as e: log("HANDLER",repr(e)); traceback.print_exc()
        except Exception as e: log("UPDATE",repr(e)); time.sleep(3)

def selftest():
    cabs=fetch_cabrios(); print("cabrios free now:",len(cabs))
    for c in cabs: print(" ",c)
    print("OK")
def main():
    if len(sys.argv)>1 and sys.argv[1]=="--selftest": selftest(); return
    if not TOKEN: print("BOT_TOKEN missing"); sys.exit(1)
    init_db(); log("bot:",tg("getMe").get("result",{}).get("username"))
    threading.Thread(target=tracking_loop,daemon=True).start()
    update_loop()
if __name__=="__main__": main()
