from __future__ import annotations

from whymoved.adapters.press_parse import decode, exhibit_url, extract_press_release

LEAD = (
    "CUPERTINO, Calif. - October 30, 2026 - Acme Corp. (Nasdaq: ACME) today announced financial "
    "results for its fiscal 2026 fourth quarter ended September 27, 2026, with record revenue."
)


def index_row(seq: str, document: str, kind: str, href: str) -> str:
    return (
        f'<tr><td scope="row">{seq}</td><td scope="row">desc</td>'
        f'<td scope="row"><a href="{href}">{document}</a></td>'
        f'<td scope="row">{kind}</td><td scope="row">1000</td></tr>'
    )


def test_the_first_readable_exhibit_99_is_chosen() -> None:
    page = (
        "<table>"
        + "".join(
            [
                "<tr><th>Seq</th><th>Description</th></tr>",
                index_row(
                    "1", "acme-8k.htm", "8-K", "/ix?doc=/Archives/edgar/data/1/2/acme-8k.htm"
                ),
                index_row("2", "slides.pdf", "EX-99.2", "/Archives/edgar/data/1/2/slides.pdf"),
                index_row("3", "ex991.htm", "EX-99.1", "/Archives/edgar/data/1/2/ex991.htm"),
            ]
        )
        + "</table>"
    )

    assert exhibit_url(page) == "https://www.sec.gov/Archives/edgar/data/1/2/ex991.htm"


def test_no_exhibit_99_means_no_address() -> None:
    page = index_row("1", "ex10.htm", "EX-10.1", "/Archives/edgar/data/1/2/ex10.htm")

    assert exhibit_url(page) is None


def test_headline_and_lead_skip_letterhead_contacts_and_boilerplate() -> None:
    document = f"""<html><body>
      <p>EX-99.1 2 ex991.htm EXHIBIT 99.1</p>
      <p>P R E S S &nbsp; R E L E A S E</p>
      <p>For Immediate Release</p>
      <p>Contact:</p><p>Jane Doe</p><p>Head, Investor Relations &amp;</p>
      <p>(555) 301-3237</p><p>jane@acme.com</p>
      <p>Acme Corp.</p>
      <p>1 Infinite Loop, Suite 100, CA 95014</p>
      <p>ACME REPORTS FOURTH QUARTER</p><p>AND FULL YEAR RESULTS</p>
      <p>October 30, 2026</p>
      <p>{LEAD}</p>
      <p>Revenue grew strongly.</p>
    </body></html>"""

    release = extract_press_release(document)

    assert release is not None
    assert release.title == "ACME REPORTS FOURTH QUARTER AND FULL YEAR RESULTS"
    assert release.summary == LEAD


def test_a_headline_broken_after_a_connecting_word_is_joined() -> None:
    document = f"<p>Acme Reports Financial Results for</p><p>First Quarter 2026</p><p>{LEAD}</p>"

    release = extract_press_release(document)

    assert release is not None
    assert release.title == "Acme Reports Financial Results for First Quarter 2026"


def test_legal_text_and_bullets_are_never_the_lead() -> None:
    legal = "Forward-Looking Statements. " + "This release contains statements. " * 6
    bullet = "● Net income for the third quarter was $84.5 million, up from last year. " * 3
    document = f"<p>Acme Announces a New Product Line</p><p>{legal}</p><p>{bullet}</p><p>{LEAD}</p>"

    release = extract_press_release(document)

    assert release is not None and release.summary == LEAD


def test_a_plain_text_exhibit_is_split_on_blank_lines() -> None:
    document = f"EXHIBIT 99.1\n\nAcme Completes Its Acquisition of Beta\n\n{LEAD}\n\nMore text."

    release = extract_press_release(document)

    assert release is not None and release.title == "Acme Completes Its Acquisition of Beta"


def test_nothing_is_returned_without_a_headline_and_a_lead() -> None:
    assert extract_press_release("<p>Exhibit 99.1</p><p>Short note.</p>") is None
    assert (
        extract_press_release("<p>Acme Announces Something Today Now</p><p>Too short.</p>") is None
    )


def test_a_long_lead_is_cut_at_a_sentence() -> None:
    long_lead = "Acme today said that it will expand. " * 30
    release = extract_press_release(f"<p>Acme Announces a Big Expansion Plan</p><p>{long_lead}</p>")

    assert release is not None
    assert len(release.summary) <= 600 and release.summary.endswith(".")


def test_decoding_falls_back_to_windows_1252() -> None:
    assert decode("café — ok".encode()) == "café — ok"
    assert decode(b"Acme \x96 results") == "Acme – results"
