# Eval report — 2026-04-27T20:02:06Z

- Prompt version: **v2**
- Agent model: `claude-sonnet-4-6`
- Judge model: `claude-opus-4-7`
- Cases: 20  (elapsed 201s)

## Track comparison

All three tracks answer the same questions. AGENT and Track A are scored against Track B with the same judge rubric (Opus 4.7). Track B is the gold reference and is shown as ground truth.

| Track | Setup | Pass% | Accuracy | Faithfulness | Citations | Refusal | Premise |
|---|---|---:|---:|---:|---:|---:|---:|
| **AGENT** | Sonnet 4.6 + Wikipedia tools | **70.0** | 1.4 | 1.65 | 1.5 | 1.45 | 1.7 |
| **Track A** | Sonnet 4.6, no tools / no internet (closed-book) | **0.0** | 0.25 | 1.4 | 0.0 | 0.6 | 1.65 |
| **Track B** | Opus 4.7 reading the gold articles | _reference_ | _2.0_ | _2.0_ | _2.0_ | _2.0_ | _2.0_ |

Track A's citations score is structurally near-zero: a closed-book model cannot cite Wikipedia articles by construction. The informative comparison is on accuracy, faithfulness, refusal calibration, and premise handling.

## By category (AGENT vs Track A)

| Category | N | AGENT pass% | AGENT acc | Track A pass% | Track A acc |
|---|---:|---:|---:|---:|---:|
| false_premise | 2 | 50.0 | 0.5 | 0.0 | 1.0 |
| multi_constraint | 5 | 80.0 | 1.8 | 0.0 | 0.6 |
| single_match | 8 | 100.0 | 2.0 | 0.0 | 0.0 |
| tiebreak_first | 1 | 0.0 | 0.0 | 0.0 | 0.0 |
| tiebreak_longest | 2 | 0.0 | 0.0 | 0.0 | 0.0 |
| tiebreak_replacement | 1 | 100.0 | 2.0 | 0.0 | 0.0 |
| tiebreak_tallest | 1 | 0.0 | 0.0 | 0.0 | 0.0 |

## Tool use

- avg_searches: 8.2
- avg_fetches: 3.6
- avg_turns: 7.4
- avg_input_tokens: 64916.2
- avg_output_tokens: 1173.8

## Failure-mode counts

- incomplete: 6
- missing_citation: 5
- wrong_fact: 2
- false_premise_accepted: 2
- no_search: 2
- wrong_citation: 1
- missed_refusal: 1

## Failing cases

### bridge-11 (tiebreak_longest)

**Q:** Among bridges completed in 2026 over the Yangtze River in China that are suspension bridges, which has the longest main span according to its Wikipedia article? Tie-breaker: the suspension bridge whose two endpoints are both Chinese cities directly bordering the Yangtze.

- accuracy=0 faithfulness=0 citations=0 refusal=0 premise=0
- failure_modes: ['incomplete', 'missing_citation']
- judge comment: The system returned an empty answer.

**Agent answer:**

> (empty)

**Reference answer:**

> Based on the excerpts, the Shuangliu Yangtze River Bridge has the longest main span at 1,430 m (4,690 ft), connecting Wuhan and Ezhou—both cities bordering the Yangtze [Shuangliu Yangtze River Bridge]. The Fuxing Yangtze River Bridge has a shorter span of 1,208 m [Fuxing Yangtze River Bridge].
> 
> However, the excerpts do not explicitly state completion dates of 2026 for any of these bridges (the Yanji bridge was expected in 2025, and no completion year is given for Shuangliu or Fuxing), so the question's premise cannot be fully verified from the provided material. Among the candidates, Shuangliu best fits the criteria given.

**Closed-book answer (Track A, no tools / no internet):**

> My training data has a cutoff of early 2025, and I don't have reliable information about bridges completed in 2026. I cannot answer this question without fabricating details.
> 
> If you need this information, I'd recommend checking Wikipedia directly for a list of Yangtze River crossings filtered by completion year and bridge type.

### bridge-12 (multi_constraint)

**Q:** Which 2026 cable-stayed bridge over the Fraser River in British Columbia, Canada, links the cities of New Westminster and Surrey and replaces the through-arch Pattullo Bridge that had spanned the river since 1937? Tie-breaker: the bridge whose official name comes from Halkomelem.

- accuracy=1 faithfulness=1 citations=2 refusal=2 premise=2
- failure_modes: ['wrong_fact', 'wrong_citation']
- judge comment: The opening date is given as January 2026 instead of February 14, 2026, and citations point to Pattullo Bridge despite the Stal̕əw̓asəm Bridge article being fetched.

**Agent answer:**

> The search snippets consistently confirm the bridge's name and details. The Pattullo Bridge article, which I successfully fetched, explicitly names the replacement. Here is the full answer:
> 
> ---
> 
> The bridge you're describing is the **stalʼəwʔasəm Bridge** (pronounced *stah-loh-AH-səm*), also known as the **Riverview Bridge** [Pattullo Bridge]. It is a cable-stayed bridge spanning the Fraser River between New Westminster and Surrey, British Columbia, that fully opened in **January 2026**, replacing the through-arch Pattullo Bridge, which had been open to traffic since **November 15, 1937** [Pattullo Bridge]. The Pattullo Bridge permanently closed to vehicular traffic on February 6, 2026, shortly after the new bridge opened [Pattullo Bridge].
> 
> As for the tie-breaker: the official name **stalʼəwʔasəm** comes directly from the **Halkomelem** language — the language of the Indigenous peoples of the Fraser River region — making it the only answer that satisfies both the engineering description and the Halkomelem name criterion [Pattullo Bridge].
> 
> Sources:
> - Pattullo Bridge

**Reference answer:**

> The stal̕əw̓asəm Bridge (also known as the Riverview Bridge) is the 2026 cable-stayed bridge over the Fraser River linking New Westminster and Surrey in British Columbia. It officially opened on February 14, 2026, replacing the through-arch Pattullo Bridge that had spanned the river since 1937. Its official name comes from Halkomelem, given by the Kwantlen First Nation and Musqueam Indian Band [Stal̕əw̓asəm Bridge].

**Closed-book answer (Track A, no tools / no internet):**

> The bridge you're describing is the **Pattullo Bridge replacement**, officially named the **King George Crossing** — wait, let me reconsider.
> 
> The replacement for the Pattullo Bridge is officially named **"Skeomish"** — actually, I need to be careful here.
> 
> From my training data, the replacement cable-stayed bridge for the Pattullo Bridge connecting New Westminster and Surrey was under construction and planned to open around 2026. The official name chosen, derived from the Halkomelem language, is **Skeomish** — I'm not fully confident in that exact spelling.
> 
> To be precise with what I know with confidence: the replacement bridge for the Pattullo Bridge is a cable-stayed bridge expected to open around 2026, and its name was selected from the Halkomelem language spoken by the local Stó:lō people. I believe the name is **"Pattullo Bridge Replacement"** in planning documents, but the official Halkomelem-derived name that was announced — I'm not certain enough of the exact name to state it definitively without risking fabrication.
> 
> I know the project exists and the Halkomelem naming detail, but I cannot confidently give you the precise official name without risking an error. If you have the answer, I'd be glad to confirm what I do or don't know about it.

### bridge-15 (tiebreak_tallest)

**Q:** Among bridges completed in 2026 according to Wikipedia, which has the tallest tower or pylon structure described in its article? Tie-breaker: the bridge that carries both rail and road traffic.

- accuracy=0 faithfulness=2 citations=0 refusal=0 premise=2
- failure_modes: ['incomplete', 'missing_citation', 'no_search']
- judge comment: The system returned an empty answer despite fetching several articles, failing to identify Ma'anshan Yangtze River Rail-Road Bridge.

**Agent answer:**

> (empty)

**Reference answer:**

> Based on the excerpts, the Ma'anshan Yangtze River Rail-Road Bridge has the tallest tower/pylon structure at 345 metres (1,132 ft), compared to the Jinqi Bridge's 300.6 metres and the Xindu Jinsha River Bridge's 297.5 metres [Ma'anshan Yangtze River Rail-Road Bridge]. It also satisfies the tie-breaker, as it is a rail-road bridge carrying both rail and road traffic [Ma'anshan Yangtze River Rail-Road Bridge].

**Closed-book answer (Track A, no tools / no internet):**

> My training data cutoff is early 2025, and I don't have reliable information about bridges completed in 2026 or their Wikipedia articles. I cannot answer this question without fabricating information.
> 
> If you need this answer, I'd recommend checking Wikipedia's lists of bridges completed in 2026 directly.

### bridge-16 (tiebreak_longest)

**Q:** Among 2026 cable-stayed bridges, which one has the longest single main span according to its Wikipedia article? Tie-breaker: the cable-stayed bridge that is projected to be the second-longest in the world when completed.

- accuracy=0 faithfulness=2 citations=0 refusal=0 premise=2
- failure_modes: ['incomplete', 'missing_citation']
- judge comment: System provided no answer at all despite having fetched the relevant article.

**Agent answer:**

> (empty)

**Reference answer:**

> Among 2026 cable-stayed bridges, the Guanyinsi Yangtze River Bridge has the longest single main span at 1,160 m (3,810 ft), and it is projected to be the second-longest cable-stayed bridge in the world when completed [Guanyinsi Yangtze River Bridge]. This satisfies the tie-breaker criterion as well.

**Closed-book answer (Track A, no tools / no internet):**

> I need to be careful here. You're asking about a specific list — "2026 cable-stayed bridges" — which sounds like it could refer to a Wikipedia category or list of cable-stayed bridges planned or under construction for completion in 2026.
> 
> I don't have reliable, specific knowledge of a Wikipedia article titled or categorized as "2026 cable-stayed bridges" or a definitive list of cable-stayed bridges scheduled for completion in 2026, nor do I have the ability to browse Wikipedia to check current article contents.
> 
> From my training data, I know that some very long-span cable-stayed bridges were planned for completion around 2026, including bridges in China. The **Huangjuetuo Yangtze River Bridge** (also called the Changhong Bridge or similar) and others were in planning/construction. However, I cannot confidently identify which specific bridge is listed on a Wikipedia "2026 cable-stayed bridges" page as having the longest single main span without risking fabrication.
> 
> Could you clarify what list or Wikipedia article you're referring to? If you mean bridges projected to open in 2026, I can try to help further, but I want to avoid making up specific span lengths or rankings that I'm not certain about.

### bridge-17 (tiebreak_first)

**Q:** Among bridges completed in 2026 according to Wikipedia, which one has the earliest 2026 opening date documented in its article? Tie-breaker: the bridge whose article gives a specific January date.

- accuracy=0 faithfulness=0 citations=0 refusal=0 premise=1
- failure_modes: ['incomplete', 'missing_citation', 'no_search', 'wrong_fact']
- judge comment: System returned an empty answer and never fetched the Xindu Jinsha River Bridge article.

**Agent answer:**

> (empty)

**Reference answer:**

> The Xindu Jinsha River Bridge has the earliest documented 2026 opening date, having opened to traffic on 1 January 2026 [Xindu Jinsha River Bridge]. This also satisfies the tie-breaker, as its article provides a specific January date.

**Closed-book answer (Track A, no tools / no internet):**

> My training data has a cutoff of early 2025, and I don't have access to Wikipedia or any web sources. I cannot look up which bridges were completed in 2026 or what opening dates their Wikipedia articles document.
> 
> I'm not able to answer this question without fabricating information, which I won't do. You would need to check Wikipedia directly for this.

### bridge-20 (false_premise)

**Q:** Which 2026 suspension bridge has a main span exceeding 1,500 metres?

- accuracy=0 faithfulness=0 citations=0 refusal=0 premise=0
- failure_modes: ['false_premise_accepted', 'missed_refusal', 'missing_citation', 'incomplete']
- judge comment: The system provided no answer at all, failing to correct the false premise or cite sources.

**Agent answer:**

> (empty)

**Reference answer:**

> Based on the provided excerpts, none of the suspension bridges listed have a main span exceeding 1,500 metres. The longest among them is the Shuangliu Yangtze River Bridge with a main span of 1,430 m [Shuangliu Yangtze River Bridge]. Additionally, the excerpts do not specify 2026 as a completion date for any of these bridges. Therefore, the excerpts do not answer the question.

**Closed-book answer (Track A, no tools / no internet):**

> My training data doesn't reliably confirm which specific suspension bridge scheduled for completion in 2026 has a main span exceeding 1,500 metres. There are several long-span bridge projects under construction globally (such as bridges in China), but I cannot confidently name the specific one you're referring to without risking providing inaccurate details.
> 
> Could you provide more context, or is there a specific bridge you have in mind? I want to avoid fabricating details.
