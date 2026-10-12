# Job sources by market (US · CA · UK · CH · UAE)

*Research date: 2026-10-12. ✓ = endpoint probed or official docs read · ⚠️ = unverified.*

**Rule:** never scrape LinkedIn, Indeed, jobs.ch, Bayt or Naukrigulf, and never let a bot open their links. All of these have terms that ban automated access.

## Source types used in every market

| Source | Access | Risk | Notes |
|---|---|---|---|
| Greenhouse / Lever / Ashby public APIs ✓ | No key | Low | AI-native companies; full description, often salary |
| **Workday** career-site JSON ✓ | `POST https://{t}.wd{N}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs`. Limit is 20 per page. Details come from `GET …/job/{externalPath}` | Low–med (undocumented, but robots.txt allows the path) | 19 tenants probed. Full description, salary range when posted, `remoteType`. Most banks, pharma and NVIDIA use it |
| **SuccessFactors** ✓ | `sitemap.xml` plus individual job pages | Low (sitemap) / med (the RSS path is disallowed in robots.txt) | Swiss Re, Zurich Insurance, Scotiabank |
| **Oracle Recruiting** ✓ | `hcmRestApi/.../recruitingCEJobRequisitions` | Med (documented as an authenticated API) | JPMorgan (611 "machine learning" hits) |
| **SmartRecruiters** ✓ | `api.smartrecruiters.com/v1/companies/{id}/postings` | Low–med | ServiceNow, Bosch, Delivery Hero |
| **SerpApi Google Jobs** ✓ | `gl` = us/ca/uk/ch/ae, plus `location` (e.g. `Zurich,Zurich,Switzerland`, `Dubai,Dubai,United Arab Emirates`) | Low for us; SerpApi carries the scraping risk | 10 results per page through `next_page_token`. `description` may be truncated ⚠️. 5 markets × 5 queries × 2 pages = 50 searches per run, so **twice a week fits the free 250/mo**. Daily runs need the $75 plan |
| **Job-alert emails** ✓ | A Gmail label read with the Gmail API (`gmail.readonly`) | Low | Personal app with fewer than 100 users. **Set the OAuth app to "In production"**: in Testing mode, refresh tokens expire after 7 days |

## Per-market extras

| Market | Use | Skip / email only |
|---|---|---|
| **USA** | USAJobs API ✓ (free key, weekly, few senior AI roles) · Adzuna US (free, 2,500 calls/mo; snippets only, so use it for counts and trends) | Big-tech career sites (Google, Microsoft, Amazon, Apple, Meta) ⚠️ terms not checked; cover them through SerpApi and alert emails |
| **Canada** | Adzuna CA (trends) | Job Bank (only 2 "ML engineer" results, no descriptions); Job Bank open data is useful only as monthly context |
| **UK** | **Reed Jobseeker API ✓**, daily: free key, full descriptions, salary · Adzuna GB | Welcome to the Jungle/Otta and Civil Service Jobs: terms or a bot check block automated access, so email alerts only |
| **Switzerland** | **swissdevjobs.ch RSS ✓**, daily: public `/rss` and `/job_feed.xml` with CHF salaries; 25 AI/ML titles today | jobs.ch / jobup.ch: terms ban automated access, so email alerts only. job-room.ch: robots.txt disallows job adverts |
| **UAE** | **SerpApi `gl=ae`** (Dubai, Abu Dhabi) as the main programmatic source · global ATS feeds filtered to the UAE | Bayt, GulfTalent, Naukrigulf: terms or Cloudflare block access, so email alerts only. Jooble is optional (medium risk) |

## Job-alert emails

**Subscribe to:**

| Market | Alerts |
|---|---|
| All | LinkedIn (the main source for CH and UAE) |
| US, CA, UK | Indeed |
| CH | jobs.ch / jobup.ch |
| UK | Reed, Otta, Civil Service Jobs |
| UAE | Bayt, GulfTalent, Naukrigulf |
| Any | Employers' own career-site alerts (lowest risk of all) |

**Pipeline:**
1. A Gmail filter puts each sender in its own label.
2. One HTML parser per sender template extracts each job card.
3. Tracking parameters are stripped.
4. Duplicates are removed, first by board + job ID, then by normalised company + title + city.

**Enrichment:** never follow the board link. Look for the employer's own copy of the posting instead:
1. Fuzzy-match against the ATS feeds already polled.
2. Or run a SerpApi query of "title + company" and follow `apply_options` to the employer's ATS.
3. For Reed postings, use the official details API.

If none of these finds the posting, keep only the fields from the email, for statistics.

**Privacy:** the data is kept private and recruiter personal data is stripped (GDPR, UK GDPR, nFADP, PIPEDA).

## Starter companies with verified job-board endpoints

| Market | Verified ✓ | To probe automatically in Phase 3 ⚠️ |
|---|---|---|
| USA | NVIDIA (Workday wd5), Salesforce (wd12), Disney (wd5), JPMorgan (Oracle Recruiting), ServiceNow (SmartRecruiters) | Anthropic, OpenAI, Palantir, Scale, Databricks, Glean, Sierra, Decagon, Harvey, Snowflake, Capital One, Goldman Sachs |
| Canada | RBC, TD, CIBC, BMO, Manulife, Sun Life (Workday wd3), Thomson Reuters (wd5), Scotiabank (SuccessFactors) | Cohere, Shopify, Wealthsimple, Ada, Clio, 1Password, Waabi, Coveo |
| UK | Barclays, Lloyds, AstraZeneca (wd3), GSK (wd5), JPMorgan London | DeepMind, Isomorphic, Wayve, Synthesia, ElevenLabs, PolyAI, Faculty, Helsing, Quantexa, Monzo; HSBC and NatWest not identified |
| Switzerland | Roche, Novartis, Julius Baer, ABB (wd3), Logitech (wd5), Swiss Re and Zurich Insurance (SuccessFactors) | Google, Microsoft, IBM Research Zurich, Scandit, Nexthink, Proton, LatticeFlow; UBS and Nestlé not identified |
| UAE | TII (Phenom), Core42 (SuccessFactors), partially verified | G42, Presight, Inception, AI71, MBZUAI, ADNOC, e&, du, Emirates NBD, FAB, Careem, Talabat |

Phase 3 builds a slug prober that tries every candidate against all of these job-board systems, so these lists grow automatically.
