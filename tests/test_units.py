import json

from conftest import fixture

from ainews.collectors import RawItem
from ainews.collectors.changelog import parse_changelog
from ainews.collectors.github_releases import PRERELEASE
from ainews.collectors.hf_papers import parse_papers
from ainews.collectors.hn import parse_hits
from ainews.collectors.openrouter import diff, summarise
from ainews.collectors.rss import parse_feed
from ainews.config import Settings
from ainews.pipeline.dedup import minhash, similarity
from ainews.pipeline.normalize import canonical_url
from ainews.pipeline.verify import _unsupported
from ainews.registry import load_sources


def test_canonical_url_strips_tracking_and_normalises():
    assert canonical_url("http://www.Example.com/a/b/?utm_source=x&id=3&ref=hn#frag") == "https://example.com/a/b?id=3"
    assert canonical_url("https://example.com/") == "https://example.com/"


def test_canonical_url_maps_papers_to_one_arxiv_id():
    expected = "https://arxiv.org/abs/2610.13166"
    assert canonical_url("https://arxiv.org/pdf/2610.13166v2") == expected
    assert canonical_url("https://huggingface.co/papers/2610.13166") == expected


def test_registry_loads_and_keyword_filter_uses_word_boundaries():
    sources = {s.id: s for s in load_sources(Settings.from_env().sources_path, include_disabled=True)}
    assert len(sources) > 40
    tc = sources["techmeme"]
    assert tc.matches("Nvidia beats estimates")
    assert tc.matches("New AI rules in the EU")
    assert not tc.matches("Said the airline")     # "ai" inside a word doesn't count
    assert sources["openai-news"].matches("anything")  # no filter


def test_parse_feed():
    items = parse_feed(fixture("feed.rss"))
    assert [i.title for i in items] == ["vLLM 0.12 adds speculative decoding for Claude-style models", "Our office dog turns five"]
    assert items[0].summary == "The release improves throughput by 1.8x on H100."
    assert items[0].published_at == "2026-10-12T08:00:00+00:00"


def test_changelog_entries_on_one_page_stay_apart():
    page = "https://docs.example.com/changelog"
    entry = "<item><title>{t}</title><link>{page}#{a}</link><guid isPermaLink='false'>{g}</guid></item>"
    rss = "<rss version='2.0'><channel><title>Changelog</title>" + "".join(
        entry.format(t=t, page=page, a=a, g=g) for t, a, g in
        [("Sep 1 - langchain", "sep-1", "g1"), ("May 12 - langchain", "may-12", "g2"), ("May 12 - langgraph", "may-12", "g3")]
    ) + "</channel></rss>"
    urls = [i.url for i in parse_feed(rss)]
    assert urls[0] == f"{page}?entry=sep-1#sep-1"
    assert len({canonical_url(u) for u in urls}) == 3      # the two May 12 entries differ by GUID


def test_podcast_episodes_link_to_their_own_page():
    from ainews.collectors.podcast import parse_lookup

    items = parse_lookup(json.dumps({"results": [
        {"wrapperType": "track", "kind": "podcast", "trackName": "The show itself"},
        {"wrapperType": "podcastEpisode", "trackName": "Open models, with a CEO", "releaseDate": "2026-10-09T17:24:00Z",
         "description": "<p>Is the future of AI open?</p>",
         "trackViewUrl": "https://podcasts.apple.com/us/podcast/open-models/id1?i=1000794029585&uo=4"}]}))
    assert [(i.title, i.summary, i.published_at) for i in items] == [
        ("Open models, with a CEO", "Is the future of AI open?", "2026-10-09T17:24:00Z")]
    assert items[0].url == "https://podcasts.apple.com/us/podcast/open-models/id1?i=1000794029585"


def test_prerelease_filter():
    titles = [i.title for i in parse_feed(fixture("releases.atom"))]
    kept = [t for t in titles if not PRERELEASE.search(t)]
    assert kept == ["v0.12.0"]
    assert not PRERELEASE.search("b6789")                       # llama.cpp build tags are real releases
    assert not PRERELEASE.search("v1.0: pre-trained weights")   # 'pre-' in prose is fine
    for t in ("v4.0.0-beta.2", "1.2.0.dev0", "nightly 2026-10-12"):
        assert PRERELEASE.search(t)


def test_parse_changelog():
    text = "# Changelog\n\n## 2.1.3\n- Fix A\n\n## 2.1.2\n- Add B\n"
    assert parse_changelog(text) == [("2.1.3", "- Fix A"), ("2.1.2", "- Add B")]


def test_parse_hf_papers_threshold():
    rows = [{"paper": {"id": "2610.1", "title": "Big", "summary": "S", "upvotes": 40}, "numComments": 3,
             "publishedAt": "2026-10-12T00:00:00Z"},
            {"paper": {"id": "2610.2", "title": "Small", "summary": "S", "upvotes": 2}}]
    items = parse_papers(json.dumps(rows), min_upvotes=10)
    assert [i.title for i in items] == ["Big"]
    assert items[0].engagement == {"upvotes": 40, "comments": 3}


def test_parse_hn_hits():
    data = {"hits": [{"objectID": "1", "title": "Show HN: thing", "url": None, "points": 150, "num_comments": 9,
                      "created_at_i": 1791848062}]}
    item = parse_hits(json.dumps(data))[0]
    assert item.url == item.discussion_url == "https://news.ycombinator.com/item?id=1"
    assert item.engagement == {"points": 150, "comments": 9}


def test_openrouter_diff_new_models_and_price_changes():
    now_ts = 1_800_000_000
    old = summarise([{"id": "a/x", "name": "X", "context_length": 128000, "created": now_ts - 10**7,
                      "pricing": {"prompt": "0.000001", "completion": "0.000002"}}])
    new = summarise([
        {"id": "a/x", "name": "X", "context_length": 128000, "created": now_ts - 10**7,
         "pricing": {"prompt": "0.0000005", "completion": "0.000002"}},
        {"id": "b/y", "name": "Y", "context_length": 1048576, "created": now_ts - 100,
         "pricing": {"prompt": "0.000004", "completion": "0.00002"}},
    ])
    items = diff(old, new, now_ts=now_ts)
    titles = sorted(i.title for i in items)
    assert titles == ["New on OpenRouter: Y", "OpenRouter price change: X"]
    assert "input $1 → $0.5" in next(i.summary for i in items if "price" in i.title)
    # First run (no snapshot): only recently created models are reported.
    assert [i.title for i in diff(None, new, now_ts=now_ts)] == ["New on OpenRouter: Y"]


def test_minhash_similarity():
    a = minhash("OpenAI releases GPT-6 with a 2M context window")
    b = minhash("OpenAI releases GPT-6 with a 2M-context window!")
    c = minhash("Nvidia reports record data center revenue")
    assert similarity(a, b) > 0.8
    assert similarity(a, c) < 0.2


def test_rawitem_defaults():
    assert RawItem(url="u", title="t").engagement == {}


def test_verify_ignores_number_formatting_but_not_new_numbers():
    src = "price per 1M tokens changed: input $0.3 -> $0.045, output $13.5; 1,000 calls"
    assert not _unsupported("Input fell from $0.30 to $0.045 and output to $13.50 over 1000 calls.", src)
    assert _unsupported("That is an 85% cut.", src) == {"85"}
