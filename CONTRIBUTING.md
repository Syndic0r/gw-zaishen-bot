# Contributing

Thanks for your interest in **GW Zaishen** - a small Discord bot that posts Guild Wars 1's daily
Zaishen Challenge Quests. This repository is the public, read-only mirror of the bot's source, so
people can read and audit the code. Issues and pull requests are welcome here and are reviewed by the
maintainer.

## Reporting a bug or requesting a feature

Open an [issue](https://github.com/Syndic0r/gw-zaishen-bot/issues/new/choose) and pick a template. For
bugs, include what you did, what you expected, and what happened (a screenshot of the message helps).

## Fixing the rotation data

If a daily quest looks wrong, it's almost always the rotation lists or anchors in
[`zaishen.py`](zaishen.py). The schedule is verified against the
[Guild Wars Wiki](https://wiki.guildwars.com/wiki/Zaishen_Challenge_Quests); please link the wiki page
for any change and update the dated cases in `tests/test_zaishen.py`.

## Development

The bot is plain Python with a small dependency set.

```bash
python -m venv venv
./venv/bin/pip install -r requirements-dev.txt -r requirements.txt
./venv/bin/ruff check .          # lint
./venv/bin/ruff format --check . # formatting
./venv/bin/python -m pytest -q   # tests (rotation validated against the wiki)
```

Please keep all three green in your PR. Match the surrounding style: clear names, comments that explain
*why*, and a test for any behaviour change.

## Scope

This repo is **bot code only**. Hosting, deployment, and the website are managed privately and aren't
part of it - so there are no secrets, infra, or CI to run here (Actions are disabled on this mirror).
Merged changes are pulled back into the maintainer's private source of truth and deployed from there.

## License

By contributing, you agree your contributions are licensed under the repository's
[LICENSE](LICENSE).
