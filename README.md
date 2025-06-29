# PixelSocial Bot

[![Latest Release](https://img.shields.io/pypi/v/pixelsocial.svg)](https://pypi.org/project/pixelsocial)
[![CI](https://github.com/deltachat-bot/pixelsocial/actions/workflows/python-ci.yml/badge.svg)](https://github.com/deltachat-bot/pixelsocial/actions/workflows/python-ci.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A micro-blogging social network via Delta Chat bot.

## Install

```sh
pip install pixelsocial
```

## Usage

To configure the bot:

```sh
pixelsocial init bot@example.org SuperHardPassword
```

**(Optional)** To customize the bot name, avatar and status/signature:

```sh
pixelsocial config selfavatar "/path/to/avatar.png"
pixelsocial config displayname "My Bot"
pixelsocial config selfstatus "Hi, I am a bot!"
```

Finally you can start the bot with:

```sh
pixelsocial serve
```

To see the available options, run in the command line:

```
pixelsocial --help
```
