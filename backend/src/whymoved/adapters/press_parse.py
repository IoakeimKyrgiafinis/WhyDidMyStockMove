"""Find the press release attached to an 8-K and pull out its headline and lead paragraph.

Companies attach the release they sent to the newswires as exhibit 99 (EX-99.1 and so on). Its
layout is free-form HTML, so the text is split into blocks at block-level tags and the headline
and lead are chosen by simple rules: skip letterhead and boilerplate, take the first sentence-like
line as the headline and the first long paragraph as the lead.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from whymoved.domain.press import PressRelease

MAX_TITLE = 300
MIN_LEAD = 140
MAX_LEAD = 600
LEAD_SEARCH_BLOCKS = 60  # the lead comes early; past this it is a table or a slide deck
CONTACT_LINES = 8

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "td",
        "th",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "center",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "head", "title"})
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_BOILERPLATE = re.compile(
    r"^(exhibit|ex-?\s*)\s*99"
    r"|^(press|news)\s+release\b"
    r"|^for\s+immediate\s+release"
    r"|^(media|investor|press)\s+(contact|relations|inquiries)"
    r"|^contacts?\b"
    r"|^source:"
    r"|^page\s+\d"
    r"|^\(?nasdaq|^\(?nyse"
    r"|^table of contents",
    re.I,
)
# A line that is only a date, a place and a date, or a phone number / web address.
_DATE_ONLY = re.compile(
    r"^[\w .,'-]{0,40}\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r" \d{1,2},? \d{4}$",
    re.I,
)
_CONTACT = re.compile(
    r"(\(\d{3}\)|\d{3}[-. ]\d{3}[-. ]\d{4}|@|www\.|https?://|\.com\b|\b(tel|fax|phone)\b)", re.I
)
_ADDRESS = re.compile(
    r"\b[A-Z]{2}\.?\s+\d{5}(-\d{4})?$"
    r"|\b(suite|street|avenue|blvd|drive|road|parkway|plaza|floor|p\.?o\.? box)\b",
    re.I,
)
# A letterhead line that is only the company's name.
_COMPANY_NAME = re.compile(
    r"\b(inc|corp|corporation|co|company|ltd|plc|llc|n\.v|s\.a|holdings|group|trust|bancorp)\.?$",
    re.I,
)
_BULLET = re.compile(r"^[•●▪■–—*·o-]\s")
_CONTACT_HEAD = re.compile(r"^((media|investor|press)\s+)?(contacts?|relations|inquiries)\b", re.I)
# Legal text that opens investor presentations and some releases: never the lead.
_LEGAL = re.compile(
    r"^(forward[- ]looking|important (notice|information)|safe harbou?r|cautionary|disclaimer"
    r"|this presentation|non-gaap|we have audited|independent auditor|information or documents)",
    re.I,
)
# A headline broken over lines ends with a word that cannot end it.
_CONTINUES = re.compile(
    r"(\b(of|for|and|or|the|to|in|on|with|a|an|at|by|from|its|reports|announces|as)|[;,:–—&-])$",
    re.I,
)


def exhibit_url(index_html: str, base: str = "https://www.sec.gov") -> str | None:
    """The address of the first exhibit-99 document listed on a filing's index page.

    Rows of the index table hold: sequence, description, document link, type, size. PDFs and
    images are skipped; an exhibit filed as plain text is kept.
    """
    for row in _ROW.findall(index_html):
        cells = [_clean(_TAG.sub(" ", c)) for c in _CELL.findall(row)]
        if len(cells) < 4 or not cells[3].upper().startswith("EX-99"):
            continue
        links = _HREF.findall(row)
        if not links:
            continue
        link = str(links[0])
        if link.startswith("/ix?doc="):  # inline-XBRL viewer link: the document itself
            link = link[len("/ix?doc=") :]
        if not link.lower().endswith((".htm", ".html", ".txt")):
            continue
        return link if link.startswith("http") else base + link
    return None


def decode(content: bytes) -> str:
    """Exhibits are UTF-8 or Windows-1252 and rarely say which: try UTF-8 first."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def extract_press_release(document: str) -> PressRelease | None:
    """The headline and lead paragraph of a press release, or None if none can be found."""
    blocks = [b for b in _drop_contacts(_blocks(document)) if not _is_noise(b)]
    title_at = next((i for i, b in enumerate(blocks) if _looks_like_title(b)), None)
    if title_at is None:
        return None
    title, end = _join_title(blocks, title_at)
    lead = next(
        (
            b
            for b in blocks[end : end + LEAD_SEARCH_BLOCKS]
            if len(b) >= MIN_LEAD and not _LEGAL.match(b) and not _BULLET.match(b)
        ),
        None,
    )
    if lead is None:
        return None
    return PressRelease(title=_shorten(title, MAX_TITLE), summary=_shorten(lead, MAX_LEAD))


def _drop_contacts(blocks: list[str]) -> list[str]:
    """Remove contact boxes: a "Contact:" line through the last phone, email or web line
    within the next few lines (names and job titles in between look like headlines)."""
    out: list[str] = []
    i = 0
    while i < len(blocks):
        if _CONTACT_HEAD.match(blocks[i]):
            window = blocks[i + 1 : i + 1 + CONTACT_LINES]
            last = max((k for k, b in enumerate(window) if _CONTACT.search(b)), default=-1)
            i += last + 2
            continue
        out.append(blocks[i])
        i += 1
    return out


def _join_title(blocks: list[str], start: int) -> tuple[str, int]:
    """The headline starting at `start`, joined with the lines it continues onto, and the
    index of the first block after it."""
    title = blocks[start]
    end = start + 1
    while end < len(blocks) and end - start < 4:
        following = blocks[end]
        shouting = title.isupper() and following.isupper()
        if not (_CONTINUES.search(title) or following[:1].islower() or shouting):
            break
        if len(title) + len(following) + 1 > MAX_TITLE or len(following) >= MIN_LEAD:
            break
        title = f"{title} {following}"
        end += 1
    return title.rstrip(";,:").strip(), end


class _BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._current: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._current.append(data)

    def _flush(self) -> None:
        text = _clean("".join(self._current))
        if text:
            self.blocks.append(text)
        self._current = []

    def close(self) -> None:
        super().close()
        self._flush()


def _blocks(document: str) -> list[str]:
    if "<" not in document[:2000]:  # a plain-text exhibit: paragraphs are separated by blank lines
        return [_clean(part) for part in re.split(r"\n\s*\n", document) if _clean(part)]
    parser = _BlockParser()
    parser.feed(document)
    parser.close()
    return parser.blocks


def _clean(text: str) -> str:
    return _SPACE.sub(" ", html.unescape(text).replace("\xa0", " ")).strip()


def _is_noise(block: str) -> bool:
    letters = sum(ch.isalpha() for ch in block)
    words = block.split()
    return (
        letters < 3
        or sum(len(w) for w in words) < 2 * len(words)  # letterhead spaced out: "P R E S S"
        or bool(_BOILERPLATE.search(block))
        or (len(block) < 80 and "exhibit 99" in block.lower())
        or bool(_DATE_ONLY.match(block))
        or (len(block) < 120 and bool(_ADDRESS.search(block)))
        or (len(block) < 250 and bool(_CONTACT.search(block)))
    )


def _looks_like_title(block: str) -> bool:
    words = block.split()
    # A company name on the letterhead ("Apple Inc.") is too short to be a headline.
    return (
        len(words) >= 4
        and len(block) <= MAX_TITLE
        and not block.endswith(":")
        and not (len(words) <= 6 and _COMPANY_NAME.search(block))
        and " today " not in block  # the dateline paragraph, not a headline
    )


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end >= limit // 2:
        return cut[: end + 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + "…"
