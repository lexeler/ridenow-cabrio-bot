# 🏎️ RideNow Cabriolet Tracker

A small, button-driven Telegram bot that watches a **public** RideNow / CarTrek
car feed for the rare **BMW 4 Cabrio** convertible (only ~2 exist on Cyprus, and
they only appear in the public map feed while they are free) and notifies you the
moment one shows up inside a radius around a location you pick.

It is a single-file Python program (standard library only — no pip
dependencies) with a clean inline-keyboard UI.

## What it does

- Tracks the **BMW 4 Cabrio** class only.
- Lets each user pick a **centre** — a Cyprus city, shared Telegram location, raw
  coordinates, or an address (geocoded via OpenStreetMap Nominatim).
- Adjustable **radius** (presets 5–100 km, or a custom value down to metres).
- Sends an alert when a cabrio **enters** the radius and when it **leaves**.
- The menu message **auto-refreshes** every poll so it always looks alive
  (live "free now" counter + last-checked clock).
- Each alert includes a **"open in app"** deep link to the specific car and a
  Google Maps link.

## How it works

A single background thread polls the public feed every `POLL_SEC` seconds,
decodes the compact car rows, filters to the cabriolet model class, computes the
great-circle distance to each user's centre, and emits enter/leave transitions.
A second thread handles Telegram long-polling for the button UI. State lives in a
local SQLite file.

## Run it

```bash
cp .env.example .env          # then put your bot token in .env
python3 bot.py                # Python 3.9+, standard library only
```

### As a systemd service

```bash
sudo cp ridenow-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ridenow-bot
```

### Config (`.env`)

| Variable       | Default | Meaning                                  |
|----------------|---------|------------------------------------------|
| `BOT_TOKEN`    | —       | Telegram bot token from @BotFather       |
| `POLL_SEC`     | `90`    | Feed poll interval (seconds)             |
| `COOLDOWN_MIN` | `30`    | Min minutes between repeat alerts per car|

## License

MIT — see [LICENSE](LICENSE). The disclaimer below applies regardless of license.

---

## ⚠️ Disclaimer — please read

This project is published **for educational and research purposes only**, as a
demonstration of consuming a public, undocumented mobile-app endpoint and
building a notification bot around it.

- It reads an endpoint of the RideNow / CarTrek app that is served **without
  authentication** (the same data the app shows on its map before you log in).
  Accessing it programmatically **may violate the RideNow / CarTrek Terms of
  Service**. **You should not run or use this software.**
- This repository accesses **only** publicly served, non-authenticated data. It
  does **not** bypass any authentication, and it deliberately does **not** touch
  any private, staff, or booking endpoints.
- The author is **not affiliated** with RideNow, CarTrek, or any related company,
  and does not endorse any misuse of their services or data.
- The software is provided **"AS IS", without warranty of any kind**. The author
  accepts **no liability** for any use, misuse, or consequences arising from this
  code. **Use is entirely at your own risk.**
- No credentials, tokens, or personal data are included in this repository.
- **If you are a rights holder** (RideNow / CarTrek) and would like this taken
  down, please open an issue and it will be removed promptly.

By cloning, forking, or running this code you accept full responsibility for
your own actions.
