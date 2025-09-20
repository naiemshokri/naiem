import os, time, json, requests, subprocess, threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from mutagen.mp3 import MP3
from colorama import init, Fore, Style

init(autoreset=True)

# -------- تنظیمات --------
CITY = "Delijan"
COUNTRY = "Iran"
METHOD = 8
TZ = ZoneInfo("Asia/Tehran")
PRAYERS = ["Fajr", "Dhuhr", "Maghrib"]
TUNE = {"Fajr": 11, "Dhuhr": 0, "Maghrib": 17}
AZAN_DIR = "azan"
MPG123_PATH = os.path.join("tools", "mpg123.exe")
NIRCMD_PATH = os.path.join("tools", "nircmd.exe")
CACHE_FILE = f"prayer_times_{datetime.now().year}.json"

STATUS_LOCK = threading.Lock()
STATUS_LINES = []

def set_status(*lines):
    global STATUS_LINES
    with STATUS_LOCK:
        STATUS_LINES = list(lines)

def clear_status():
    set_status()

# -------- دریافت اوقات شرعی --------
def internet_ok():
    try:
        requests.get("https://www.google.com", timeout=4)
        return True
    except:
        return False

def fetch_year(year):
    url = "https://api.aladhan.com/v1/calendarByCity"
    params = {
        "city": CITY, "country": COUNTRY,
        "method": METHOD, "timezonestring": "Asia/Tehran",
        "year": year
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    raw = r.json()
    out = {}
    for d in raw["data"]:
        key = d["date"]["gregorian"]["date"]
        out[key] = {p: d["timings"][p].split()[0] for p in PRAYERS}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"year": year, "data": out}, f, ensure_ascii=False, indent=2)
    return out

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)["data"]
    except:
        return None

def get_today_timings():
    year = datetime.now(TZ).year
    key = datetime.now(TZ).strftime("%d-%m-%Y")
    db = load_cache()
    if not db and internet_ok():
        db = fetch_year(year)
    return db.get(key) if db else None

def apply_tune(timings):
    today = datetime.now(TZ).date()
    out = {}
    for p, t in timings.items():
        h, m = map(int, t.split(":"))
        dt = datetime(today.year, today.month, today.day, h, m, tzinfo=TZ) + timedelta(minutes=TUNE.get(p, 0))
        out[p] = dt.strftime("%H:%M")
    return out

def next_three(timings):
    now = datetime.now(TZ)
    today = now.date()
    items = []
    for p in PRAYERS:
        hh, mm = map(int, timings[p].split(":"))
        t = datetime(today.year, today.month, today.day, hh, mm, tzinfo=TZ)
        if t <= now:
            t += timedelta(days=1)
        items.append((p, t))
    return sorted(items, key=lambda x: x[1])

# -------- کنترل VLC --------
def vlc_mute():
    try:
        subprocess.run([NIRCMD_PATH, "muteappvolume", "vlc.exe", "1"], check=False, shell=True)
    except Exception as e:
        set_status(Fore.RED + f"[!] Failed to mute VLC: {e}")

def vlc_unmute():
    try:
        subprocess.run([NIRCMD_PATH, "muteappvolume", "vlc.exe", "0"], check=False, shell=True)
    except Exception as e:
        set_status(Fore.RED + f"[!] Failed to unmute VLC: {e}")

# -------- پخش اذان --------
def play_azan_with_status(prayer, t_prayer):
    mp3 = os.path.join(AZAN_DIR, f"{prayer.lower()}.mp3")
    if not os.path.exists(mp3):
        set_status(Fore.RED + f"[!] Missing file: {mp3}")
        return

    try:
        duration = max(1, int(MP3(mp3).info.length))
    except:
        duration = 90

    pre_mute_start = t_prayer - timedelta(seconds=60)

    # صبر تا شروع میوت قبل از اذان
    while True:
        now = datetime.now(TZ)
        dt = (pre_mute_start - now).total_seconds()
        if dt <= 0:
            break
        mm, ss = divmod(int(dt), 60)
        set_status(Fore.YELLOW + f"⏳ MUTE starts in {mm:02d}:{ss:02d} (before {prayer})")
        time.sleep(0.5)

    # شروع میوت
    vlc_mute()

    # کول‌دان تا اذان
    while True:
        now = datetime.now(TZ)
        dt = (t_prayer - now).total_seconds()
        if dt <= 0:
            break
        mm, ss = divmod(int(dt), 60)
        set_status(Fore.YELLOW + f"🔇 VLC muted pre-azan for {prayer}",
                   Fore.CYAN + f"→ To azan: {mm:02d}:{ss:02d}")
        time.sleep(0.5)

    # پخش اذان
    try:
        proc = subprocess.Popen([MPG123_PATH, mp3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        set_status(Fore.RED + f"Error playing azan: {e}")
        return

    start = time.time()
    label = "{--playing " + f"{prayer}" + " azan--}"

    # نمایش درصد پخش تا پایان
    while True:
        elapsed = time.time() - start
        percent = min(100, int((elapsed / duration) * 100))
        set_status(Fore.YELLOW + f"🔇 VLC muted {label}[{percent}%]")
        if proc.poll() is not None or elapsed >= duration:
            break
        time.sleep(1)

    # اطمینان از توقف پخش
    try:
        if proc.poll() is None:
            proc.terminate()
    except:
        pass

    # بعد از پایان اذان → کول‌دان ۶۰ ثانیه‌ای
    for sec in range(60, 0, -1):
        set_status(Fore.YELLOW + f"🔇 VLC muted - unmute in 00:{sec:02d}")
        time.sleep(1)

    # آن‌میوت
    vlc_unmute()
    set_status(Fore.GREEN + f"🔊 VLC unmuted after cooldown post-{prayer} azan.")
    time.sleep(3)
    clear_status()

# -------- حلقه اصلی --------
def main():
    timings = get_today_timings()
    if not timings:
        print(Fore.RED + "ERROR: No prayer timings available.")
        return
    timings = apply_tune(timings)
    order = next_three(timings)

    started_keys = set()
    rolled_keys = set()

    try:
        while True:
            now = datetime.now(TZ)
            next_p, next_t = order[0]
            key = f"{next_p}@{next_t.isoformat()}"

            secs_to_next = int((next_t - now).total_seconds())
            if 0 <= secs_to_next <= 60 and key not in started_keys:
                threading.Thread(target=play_azan_with_status, args=(next_p, next_t), daemon=True).start()
                started_keys.add(key)

            if (now - next_t).total_seconds() >= 2 and key not in rolled_keys:
                order[0] = (next_p, next_t + timedelta(days=1))
                order.sort(key=lambda x: x[1])
                rolled_keys.add(key)
                if len(started_keys) > 30:
                    started_keys = set(list(started_keys)[-10:])
                if len(rolled_keys) > 30:
                    rolled_keys = set(list(rolled_keys)[-10:])

            os.system("cls" if os.name == "nt" else "clear")
            print(Fore.GREEN + Style.BRIGHT + f" ▌│█║▌║▌ Naiemshokri@gmail.com ▌║▌║█│▌")
            print(Fore.GREEN + Style.BRIGHT + f"╔════════════════════════════════════╗")
            print(Fore.GREEN + Style.BRIGHT + f"║    Homaye Sa'adat Azan Scheduler   ║")
            print(Fore.GREEN + Style.BRIGHT + f"╠════════════════════════════════════╣")
            print(Fore.GREEN + f"║ Time Now: {now.strftime('%H:%M:%S')}                 ║")
            delta = max(0, int((next_t - now).total_seconds()))
            hh, rem = divmod(delta, 3600)
            mm, ss = divmod(rem, 60)
            print(Fore.RED   + f"║ Next Prayer: {next_p} in {hh:02d}:{mm:02d}:{ss:02d}")
            print(Fore.GREEN + f"╠────────────────────────────────────╣")
            for i, (p, t) in enumerate(order, 1):
                left = max(0, int((t - now).total_seconds()))
                h2, rem2 = divmod(left, 3600)
                m2, _ = divmod(rem2, 60)
                print(Fore.GREEN + f"║ {i}. {p:<8} at {t.strftime('%H:%M')} → {h2:02d}:{m2:02d} left  ║")
            print(Fore.GREEN + f"╚════════════════════════════════════╝")

            # نمایش وضعیت‌های زنده
            with STATUS_LOCK:
                if STATUS_LINES:
                    print()
                    for line in STATUS_LINES:
                        print(line)

            time.sleep(1)
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n[!] Program terminated by user.")

if __name__ == "__main__":
    main()

