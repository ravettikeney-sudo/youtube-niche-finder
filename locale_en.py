"""English labels for the shared, server-rendered interface."""
from functools import lru_cache
from html import escape, unescape
import json
from pathlib import Path
import re

MESSAGES = json.loads((Path(__file__).parent / "locales" / "en.json").read_text(encoding="utf-8"))
DESCRIPTION = ("Find a YouTube niche, discover growing channels and breakout video ideas, "
               "analyze competitors, test thumbnails, and review scripts against your channel's hits.")

# Only literal text and presentational attributes in the shared static template are translated.
# IDs, option values, links, and report content are independent of the interface language.
MARKUP = re.compile(r"""<!--[\s\S]*?-->|<(?:[^"'<>]|"[^"]*"|'[^']*')+>|[^<]+|<""")
LABEL = re.compile(r'(\s(?:title|aria-label|placeholder|alt|data-nav|content)=")([^"]*)(")')


def _translation(value):
    return MESSAGES.get(" ".join(unescape(value).split()))


def _attribute(match):
    original = match[2]
    translated = _translation(original)
    return match[1] + (escape(translated, quote=True) if translated is not None else original) + match[3]


@lru_cache(maxsize=2)
def render(raw):
    def replace(match):
        token = match[0]
        if token.startswith("<!--"):
            return token
        if token.startswith("<"):
            return LABEL.sub(_attribute, token)
        translated = _translation(token)
        if translated is None:
            return token
        leading = token[:len(token) - len(token.lstrip())]
        trailing = token[len(token.rstrip()):]
        return leading + escape(translated, quote=False) + trailing

    return MARKUP.sub(replace, raw)
