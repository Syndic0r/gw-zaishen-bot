# GW Zaishen

A small Discord bot that posts **Guild Wars 1**'s four daily **Zaishen Challenge Quests**
(Mission · Bounty · Combat · Vanquish) and keeps the message pinned to the bottom of a channel, with
per-quest sign-up buttons so your crew can say who's up for today's run.

**→ [gw-zaishen-bot.duckdns.org](https://gw-zaishen-bot.duckdns.org/)** - what it does + add it to your server.

This repository holds the **bot's source code**, published so anyone can read and audit it. It's the
part of the project that runs in Discord; the website and deployment live elsewhere.

## What it does

- Computes the day's quests **offline** from the verified rotation cycles in
  [`zaishen.py`](zaishen.py) - no scraping, no external API. Quests change at **16:00 UTC** (fixed,
  no DST), matching the in-game Zaishen reset.
- Posts one message per server in the channel an admin picks with `/setup`, and **keeps it as the
  most recent message** (edits in place, reposts at the bottom if chat pushes it up).
- **Per-mission sign-up** - one toggle button per daily (🗺️🎯⚔️💀), plus ✅ All and 🧹 Sign off all.
  Each quest shows its own roster.
- Remembers rosters (with timestamps) per server in SQLite; `/history` looks back over past days.
- Optional self-declared GW1 character names via `/ign` (a GW1 account has several; pick a favorite
  to show next to your handle on the roster).
- No privileged intents, no message reading, no analytics - it only posts in one channel and manages
  its own message.

## Commands

| Command | Who | What |
|---|---|---|
| `/zaishen` | anyone | Show today's dailies (ephemeral) |
| `/history show [days]` | anyone | Recent days' sign-ups in this server |
| `/ign add\|remove\|favorite\|unfavorite\|who\|clear` | anyone | Register your GW1 character names; pick a favorite to show on the roster |
| `/setup #channel [role]` | admin | Choose the post channel + optional daily ping role |
| `/enable` · `/disable` | admin | Resume / pause posting in this server |
| `/history clear\|enable\|disable` | admin | Clear stored history / toggle keeping it |
| `/adminrole [role]` | Manage Server | Let a role use the admin commands |

"admin" = **Manage Server** or the role set via `/adminrole`.

## Run it yourself

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp gw-zaishen-bot.conf.example my.conf      # fill in DISCORD_BOT_TOKEN
set -a && . ./my.conf && set +a
./venv/bin/python bot.py
```

Then invite the bot and run `/setup #channel` in your server. Required channel permissions: View
Channel, Send Messages, Embed Links, Read Message History, Manage Messages.

### Tests

The rotation is validated against the published [Guild Wars Wiki](https://wiki.guildwars.com/wiki/Zaishen_Challenge_Quests)
schedule:

```bash
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest -q
```

## Modules

| File | Responsibility |
|---|---|
| [`bot.py`](bot.py) | entrypoint - Discord client, the per-server keep-at-bottom loop, the slash commands |
| [`zaishen.py`](zaishen.py) | rotation domain - which quests are active (pure, no deps) |
| [`storage.py`](storage.py) | persistence (SQLite) - per-guild config, message, signups, history, IGNs |
| [`render.py`](render.py) | the daily message text |
| [`views.py`](views.py) | the per-mission buttons + the paged-list helper |
| [`commands.py`](commands.py) | the `/ign` command group |
| [`config.py`](config.py) | environment/config + shared constants |

## License

[MIT](LICENSE). Not affiliated with or endorsed by ArenaNet or NCSOFT. Guild Wars is a trademark of
NCSOFT Corporation; this is a fan-made tool.
