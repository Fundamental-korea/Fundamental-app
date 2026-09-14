"""Diagnostic for SEC Inline XBRL parsing.

Read-only: downloads one annual SEC HTML filing, parses it in memory, and
prints structural counts/samples. Raw SEC content is never persisted.
"""
from __future__ import annotations

import sys
from collections import Counter

from lxml import html

from collector_us_fundamental import load_company
from sec_xbrl_search_v2_3_2 import SECXBRLSearchV2_3_2
from sec_xbrl_inline import _attr, _local, _first_text, _num


def main(ticker: str = "HON"):
    resolver = SECXBRLSearchV2_3_2()
    company = load_company(ticker)
    cik = str(company["cik"] if isinstance(company, dict) else company.cik)
    submissions = resolver.submissions(cik)
    accession, doc, filed = resolver.latest_annual_filing(submissions)
    compact = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{doc}"
    text = resolver._get(url).text
    root = html.fromstring(text.encode("utf-8"))

    print(f"ticker={ticker} cik={cik} accession={accession} doc={doc} filed={filed}")
    print(f"root_tag={root.tag}")

    tags = Counter(_local(e.tag) for e in root.iter() if isinstance(e.tag, str))
    print(f"context_count={tags['context']}")
    print(f"unit_count={tags['unit']}")
    print(f"nonfraction_count={tags['nonfraction']}")
    print(f"nonfractionrelated_count={sum(v for k,v in tags.items() if 'nonfraction' in k)}")

    print("top_tags:")
    for name, count in tags.most_common(25):
        print(f"  {name}: {count}")

    contexts = []
    for e in root.iter():
        if _local(e.tag) == "context":
            contexts.append(e)
    print(f"contexts_parsed={len(contexts)}")
    for e in contexts[:3]:
        print("CONTEXT", {"id": _attr(e, "id"), "instant": _first_text(e, "instant"), "start": _first_text(e, "startDate"), "end": _first_text(e, "endDate")})

    facts = []
    for e in root.iter():
        if _local(e.tag) == "nonfraction":
            facts.append(e)
    print(f"nonfraction_samples={min(10, len(facts))}")
    for e in facts[:10]:
        raw_text = " ".join(e.itertext()).strip()
        print("FACT", {
            "tag": e.tag,
            "name": _attr(e, "name"),
            "contextRef": _attr(e, "contextRef"),
            "unitRef": _attr(e, "unitRef"),
            "scale": _attr(e, "scale"),
            "sign": _attr(e, "sign"),
            "nil": _attr(e, "nil"),
            "text": raw_text[:120],
            "num": _num(raw_text, _attr(e, "scale"), _attr(e, "sign")),
        })


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "HON")
