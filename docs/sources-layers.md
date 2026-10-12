# Verified sources: the big-picture and low-level layers

*Every feed was fetched on 2026-10-12. These sources fill the gaps flagged in [architecture.md](architecture.md) ("Coverage").*

**Status:**
- ✅ valid RSS/Atom with an item in the last 30 days
- ⚠️ works only through a workaround (proxy, key, community scraper or user-agent trick), or no new item in the last 30 days
- ❌ dead or no feed

**Content:** HL = headline or teaser only · Full = full text · PW = paywalled

## Layer 1: Industry, business, funding

| Source | Feed | Status | Cadence | Signal | Notes |
|---|---|---|---|---|---|
| The Information | `https://www.theinformation.com/feed` | ✅ | ~7/day | high | PW; the Atom feed includes a free summary |
| Bloomberg Tech | `https://feeds.bloomberg.com/technology/news.rss` | ✅ | ~3/day | high | PW, HL |
| Reuters (no RSS) | Google News query `site:reuters.com "artificial intelligence" when:7d` | ⚠️ proxy | ~15/day | high | Resolve the redirect links before dedup |
| TechCrunch AI | `https://techcrunch.com/category/artificial-intelligence/feed/` | ✅ | ~7/day | high | HL |
| Crunchbase News AI | `https://news.crunchbase.com/sections/ai/feed/` | ✅ | ~1/day | high | Full; funding data |
| Techmeme | `https://www.techmeme.com/feed.xml` | ✅ | ~15/day | high | Good signal for ranking and dedup |
| Stratechery | `https://stratechery.com/feed/` | ✅ | ~5/wk | high | PW, HL |
| CB Insights | `https://www.cbinsights.com/research/feed/` | ✅ | ~3/day | med | HL |
| FT Tech | `https://www.ft.com/technology?format=rss` | ✅ | ~8/day | med | PW, HL |
| Semafor | `https://www.semafor.com/rss.xml` | ✅ | ~20/day | med | Filter by category: Technology, Gulf |
| Axios | `https://api.axios.com/feed/` | ✅ | ~13/day | med | Filter by keyword |
| VentureBeat | `https://venturebeat.com/feed/` | ⚠️ UA | ~2/day | med | Needs a feed-reader User-Agent |
| BetaKit (Canada) | `https://betakit.com/feed/` | ✅ | ~4/day | med | Full |
| Sifted (EU/CH) | `https://sifted.eu/feed` | ✅ | ~3/day | med | Titles only |
| The National, Business (UAE) | `https://www.thenationalnews.com/arc/outboundfeeds/rss/category/business/?outputType=xml` | ✅ | ~2/day | med | Full |

## Layer 1b: Policy, regulation, safety (US · CA · UK · CH · UAE · EU)

| Market | Source | Feed | Status | Signal |
|---|---|---|---|---|
| US | Federal Register (AI) | `https://www.federalregister.gov/api/v1/documents.rss?conditions%5Bterm%5D=%22artificial+intelligence%22&order=newest` | ✅ | high |
| US | White House presidential actions | `https://www.whitehouse.gov/presidential-actions/feed/` | ✅ | med |
| US | NIST news (CAISI has no feed of its own) | `https://www.nist.gov/news-events/news/rss.xml` | ✅ | med |
| US | Congress.gov bills | `https://api.congress.gov/v3/bill` | ⚠️ needs a free api.data.gov key | med |
| US states | StateScoop | `https://statescoop.com/feed/` | ✅ | med |
| US states | CA + NY AI bills (Google News query) | `(California OR "New York") AI (bill OR law OR "RAISE Act" OR "SB 53") when:14d` | ⚠️ proxy | med |
| UK | AI Security Institute on GOV.UK | `https://www.gov.uk/government/organisations/ai-security-institute.atom` | ✅ | high |
| UK | AISI blog (community scraper) | `https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_aisi.xml` | ⚠️ third party | high |
| UK | DSIT | `https://www.gov.uk/government/organisations/department-for-science-innovation-and-technology.atom` | ✅ | med |
| Canada | ISED news | `https://api.io.canada.ca/io-server/gc/news/en/v2?dept=departmentofindustry&sort=publishedDate&orderBy=desc&pick=50&format=atom&atomtitle=ISED` | ✅ | high |
| Canada | Canada AI policy (Google News query) | `Canada "artificial intelligence" (minister OR regulation OR policy) when:14d` | ⚠️ proxy | med |
| CH | Federal news service (filter for AI/KI) | `https://www.newsd.admin.ch/newsd/feeds/rss?lang=en&org-nr=1&kind=M` | ✅ | med |
| CH | AlgorithmWatch CH | `https://algorithmwatch.ch/en/rss` | ✅ | med |
| UAE | UAE AI (Google News query; WAM and MBZUAI have no usable feed) | `(MBZUAI OR G42 OR "UAE AI" OR Stargate UAE) when:7d` | ⚠️ proxy | med |
| EU | EU AI Act Newsletter | `https://artificialintelligenceact.substack.com/feed` | ✅ | high |
| EU | EC Digital Strategy (AI Office) | `https://digital-strategy.ec.europa.eu/en/rss.xml` | ✅ | high |
| EU | Politico Europe Tech | `https://www.politico.eu/section/technology/feed/` | ✅ | high |
| Safety | Transformer | `https://www.transformernews.ai/feed` | ✅ | high |
| Safety | METR | `https://metr.org/feed.xml` | ✅ | high |
| Policy | Tech Policy Press (4k-item feed; use conditional GET) | `https://techpolicy.press/rss/feed.xml` | ✅ | med |
| Policy | GovAI | `https://www.governance.ai/post/rss.xml` | ✅ | med |

**Dead:** WSJ Tech RSS (frozen since 2025-01), Euractiv (blocked by Cloudflare), the NY Governor and NY Senate sites (bot wall).

## Layer 4a: Compute, hardware, datacenters, energy

| Source | Feed | Status | Signal | Notes |
|---|---|---|---|---|
| SemiAnalysis | `https://newsletter.semianalysis.com/feed` | ✅ | high | Mostly PW; free intros |
| Chips and Cheese | `https://chipsandcheese.com/feed/` | ✅ | high | Full |
| NVIDIA Technical Blog | `https://developer.nvidia.com/blog/feed/` | ✅ | high | ~10/wk |
| AMD ROCm Blogs | `https://rocm.blogs.amd.com/blog/atom.xml` | ✅ | high | |
| ServeTheHome | `https://www.servethehome.com/feed/` | ✅ | high | |
| The Next Platform | `https://www.nextplatform.com/feed/` | ✅ | high | |
| Latitude Media (AI and the power grid) | `https://www.latitudemedia.com/feed` | ✅ | high | Full |
| NVIDIA Newsroom | `https://nvidianews.nvidia.com/releases.xml` | ✅ | med | Press releases |
| Google Cloud Compute / TPU | `https://cloudblog.withgoogle.com/products/compute/rss/` | ✅ | med | |
| DCD | `https://www.datacenterdynamics.com/en/rss/` | ✅ | med | ~20/day; filter |
| Data Center Frontier | see the research notes | ✅ | med | |
| Heatmap (energy) | `https://heatmap.news/feeds/feed.rss` | ✅ | med | Filter for "data center" |
| TrendForce Semiconductors | `https://www.trendforce.com/feed/Semiconductors.html` | ✅ | med | HBM and foundry pricing |
| MLCommons | `https://mlcommons.org/feed/` | ✅ | med | MLPerf rounds |

## Layer 4b: Low-level ML systems

| Source | Feed | Status | Signal | Notes |
|---|---|---|---|---|
| PyTorch Blog | `https://pytorch.org/blog/feed/` | ✅ | high | |
| PyTorch dev-discuss | `https://dev-discuss.pytorch.org/latest.rss` | ✅ | high | Compiler and Inductor RFCs |
| vLLM Blog | `https://vllm.ai/blog/rss.xml` | ✅ | high | |
| LMSYS / SGLang blog | `https://github.com/lm-sys/lm-sys.github.io/commits/main.atom` | ⚠️ proxy | high | No RSS; repo commits roughly track new posts |
| Kernel and serving releases | `releases.atom` for triton-lang/triton, Dao-AILab/flash-attention, NVIDIA/cutlass, NVIDIA/TensorRT-LLM, unslothai/unsloth | ✅ | high | |
| GPU MODE (YouTube) | `https://www.youtube.com/feeds/videos.xml?channel_id=UCJgIbYl6C5no72a0NUAPcTA` | ✅ | high | Intermittent 404s, so retry |
| FlashInfer | `https://flashinfer.ai/feed.xml` | ✅ | high | ~monthly |
| Together AI | `https://www.together.ai/blog/rss.xml` | ✅ | med | |
| Modal | `https://modal.com/blog/atom.xml` | ✅ | med | |
| Character.AI | `https://blog.character.ai/rss/` | ✅ | med | Inference efficiency |
| Thinking Machines | `https://thinkingmachines.ai/blog/index.xml` | ⚠️ infrequent | high | ~every 6 weeks |
| Colfax Research | `https://research.colfax-intl.com/feed/` | ⚠️ infrequent | high | CUTLASS/FA3 tutorials |
| Lei Mao | `https://leimao.github.io/feed.xml` | ✅ | high | CUDA / TensorRT |
| Sebastian Raschka | `https://magazine.sebastianraschka.com/feed` | ✅ | high | |
| Tim Dettmers | `https://timdettmers.com/feed/` | ✅ | high | Posts rarely |
| Jianlin Su (kexue.fm, Chinese) | `https://kexue.fm/feed` | ✅ | high | Optimizers and attention math |
| gau-nernst | `https://gau-nernst.github.io/index.xml` | ✅ | med | Kernel write-ups |
| Stas Bekman ml-engineering | `https://github.com/stas00/ml-engineering/commits/master.atom` | ✅ | med | Noisy |
| Tri Dao | `https://tridao.me/feed.xml` | ⚠️ infrequent | high | Posts rarely |

**No feed:** Fireworks, Baseten, Hazy Research. Horace He now publishes through Thinking Machines.

## Starter set (added to the source registry in Phase 1)

**Layers 1 and 1b (15 sources):**
1. The Information
2. Bloomberg Tech
3. Reuters (via Google News)
4. TechCrunch AI
5. Crunchbase News AI
6. Semafor (Technology and Gulf)
7. Federal Register
8. StateScoop
9. CA + NY AI bills query
10. EU AI Act Newsletter
11. UK AISI (GOV.UK and blog)
12. Canada ISED
13. Swiss federal news service (AI filter)
14. UAE AI query
15. Transformer

**Layers 4a and 4b (15 sources):**
1. SemiAnalysis
2. Chips and Cheese
3. NVIDIA Technical Blog
4. AMD ROCm Blogs
5. ServeTheHome
6. The Next Platform
7. Latitude Media
8. PyTorch Blog
9. PyTorch dev-discuss
10. vLLM Blog
11. LMSYS commits
12. Kernel-releases bundle
13. GPU MODE
14. Lei Mao
15. Thinking Machines

**Cheap extras:** Tri Dao, Tim Dettmers, Raschka, kexue.fm, Colfax, FlashInfer. They post rarely, but every post is high-signal.

**Ingestion notes:**
- Send a feed-reader User-Agent.
- Always use conditional GET (ETag / If-Modified-Since).
- Resolve Google News redirect links before dedup.
- Watch the community-scraped AISI feed in the source health check.
