# Building a high-quality personal knowledge graph: a research review for Lumogis

> **Navigation:** Product-aligned KG quality framing lives in [`lumogis_kg_quality_strategy.md`](lumogis_kg_quality_strategy.md) and ADRs under [`docs/decisions/`](decisions/). This file is a retained long-form research review.

## 1. Executive summary

**The central finding of this review is that the core quality problems Lumogis faces — duplicate entities, noisy extractions, spurious edges, and concept drift — are well-studied at enterprise scale but significantly under-addressed for local, single-user personal knowledge graphs.** The good news: mature, lightweight solutions exist for each problem when adapted to the personal KG context. The constraints of local deployment, privacy-first design, and continuous ingestion narrow the solution space in productive ways, eliminating enterprise-grade complexity in favour of simpler, faster approaches that work well at the scale of thousands to tens of thousands of entities.

The most critical insight across all research domains is that **hybrid approaches consistently outperform pure rule-based or pure ML methods** for entity resolution, noise filtering, and relation quality — and that Lumogis's existing architecture (Postgres + Qdrant + FalkorDB) already provides the infrastructure for an effective hybrid pipeline. Splink, an unsupervised probabilistic record linkage library, emerges as the strongest open-source tool for entity resolution at this scale. For edge quality, sentence-level co-occurrence filtered by Pointwise Mutual Information (PMI) is the most defensible baseline, supplemented by an exponential temporal decay model. For concept drift, a bi-temporal entity profile with alias tracking and periodic consistency heuristics provides robust drift management without heavy ML.

**Top three recommendations for Lumogis:**

1. **Adopt a 4-stage entity resolution pipeline** — deterministic rules (exact match + nickname lookup) → embedding-based blocking via Qdrant → Splink probabilistic scoring → human review queue for ambiguous pairs. This maps directly onto the existing 3-tier architecture and adds the missing blocking stage.

2. **Implement PMI-filtered, sentence-level co-occurrence with per-edge-type temporal half-lives** — replace raw co-occurrence counts with statistical significance scoring, use paragraph-level as a secondary signal at lower confidence, and apply exponential decay with configurable half-lives (e.g., 30 days for DISCUSSED_IN, 365 days for DERIVED_FROM).

3. **Build a lightweight graph health dashboard** tracking six automated signals: duplicate candidate count, orphan entity percentage, mean entity completeness, constraint violation count, ingestion quality trend, and temporal freshness distribution. Surface a prioritised review queue of the **top 10 items needing human attention** each week.

---

## 2. Entity resolution

### The foundational theory maps directly to Lumogis's 3-tier pipeline

Entity resolution (ER) — also called record linkage, deduplication, or entity matching — has a formal foundation dating to **Fellegi and Sunter (1969)**, who proved that the optimal decision framework for matching records under uncertainty produces exactly three outcomes: link, non-link, and possible link requiring manual review. This maps cleanly onto Lumogis's existing 3-tier pipeline (merge / flag ambiguous / create new). Fellegi-Sunter computes a likelihood ratio for each comparison pair, comparing the probability of observing the comparison vector among true matches versus true non-matches, and applies upper and lower thresholds to partition decisions.

**Winkler (1988, 1989)** extended this framework with EM algorithm parameter estimation, enabling unsupervised learning of match weights without labeled training data — a critical feature for Lumogis, where generating labeled entity pairs would be burdensome for a single user. This is the approach implemented in modern tools like Splink.

The field has since evolved through deterministic rules, probabilistic models, supervised ML (random forests, gradient boosting), and deep learning approaches. The most comprehensive recent surveys include **Christophides, Efthymiou, Palpanas, Papadakis, and Stefanidis (2020)** in *ACM Computing Surveys* and **Papadakis et al. (2020)** on blocking and filtering techniques. The deep learning era produced **DeepMatcher** (Mudgal et al., SIGMOD 2018), which uses RNNs with attention, and **Ditto** (Li et al., PVLDB 2020), which applies pre-trained transformers (BERT/DistilBERT) and achieves up to 29% F1 improvement over previous state-of-the-art — though both require labeled training data. **ZeroER** (Wu et al., SIGMOD 2020) offers an unsupervised alternative using Gaussian Mixture Models on similarity feature vectors.

### Personal name resolution demands a multi-signal approach

Resolving personal names is the hardest ER sub-problem for a personal knowledge graph. Names exhibit nicknames (Bob ↔ Robert), initials (R. Smith), abbreviations, misspellings, cultural ordering variations, and title changes. No single technique handles all of these.

**Jaro-Winkler similarity** is widely regarded as the best single edit-distance metric for personal names, giving extra weight to matching prefixes (the first four characters). **Phonetic algorithms** (Soundex, Double Metaphone, NYSIIS) are fast but cannot handle nicknames — "Bob" and "Robert" are phonetically dissimilar. **Embedding-based approaches** using sentence-transformers can capture some semantic similarity but struggle with name-specific variations without fine-tuning.

The practical solution is a **multi-signal pipeline**: normalise name components using a parser like `python-nameparser`, expand abbreviations and initials, look up nicknames in a hypocorism database (the Python `nicknames` library provides equivalence classes such as Bob → Robert → Rob → Bobby), compute Jaro-Winkler similarity on name components, and optionally use embedding cosine similarity as a secondary signal. For Lumogis specifically, building a **personal alias table** from observed usage patterns — learning that in this user's data, "Bob" always co-occurs with "Robert Smith" — is likely more valuable than any general-purpose name matching algorithm.

### Blocking at Lumogis scale is straightforward but benefits from Qdrant

Without blocking, comparing N entities requires N(N−1)/2 pairs — **50 million comparisons for just 10,000 entities**. Blocking reduces this by grouping likely matches. The definitive survey is Papadakis et al. (2020) in *ACM Computing Surveys*.

At Lumogis's scale (thousands to low tens of thousands), blocking is dramatically simpler than at enterprise scale. **Type-based blocking alone** (comparing Person only to Person, Organisation only to Organisation) reduces comparisons by an order of magnitude. Adding simple attribute-based blocking (first two characters of name, Soundex code) further narrows candidates.

The most elegant option for Lumogis is **embedding-based blocking via Qdrant**. Since entity name embeddings are already stored in the vector database, an approximate nearest-neighbour search retrieves the top-k most similar entities as candidate matches — this is effectively LSH blocking using Qdrant's HNSW index. A multi-pass approach combining attribute blocking and vector similarity blocking maximises recall. Meta-blocking and more sophisticated methods are overkill below 100,000 entities.

### The case for a hybrid approach is overwhelming

The consensus across both academic literature and industry practice is clear: **hybrid approaches combining rules, embeddings, and probabilistic scoring outperform any single method**. For Lumogis's local/offline context, this translates to a concrete architecture:

- **Tier 1 — Deterministic**: Exact match on normalised name + known alias lookup → auto-merge. Cost: microseconds per comparison.
- **Tier 2 — Blocking + fuzzy matching**: Qdrant ANN search for candidates → Jaro-Winkler on name components + nickname database + initial expansion → compute match score. Cost: ~1ms per query.
- **Tier 3 — Contextual scoring**: Co-occurrence in documents, shared graph relationships, embedding cosine similarity → boost or penalise score.
- **Tier 4 — Ambiguity resolution**: Pairs scoring in the ambiguous range enter a human review queue; optionally, a local LLM (Llama 3.2 3B or Phi-3-mini) can provide a preliminary judgment at ~1–10 seconds per pair.

Local embedding models suitable for this pipeline include **all-MiniLM-L6-v2** (22M parameters, 384-dim, runs fast on CPU), **BAAI/bge-small-en-v1.5**, and **nomic-embed-text**. A critical practical insight: embed structured context rather than raw names — "Person: Bob Smith, associated with Project Alpha, mentioned in meeting notes 2024" produces far fewer false positives than embedding "Bob Smith" alone.

### Splink is the recommended primary tool

Among open-source ER tools, **Splink** stands out for Lumogis. Developed by the UK Ministry of Justice and downloaded over 7 million times, it implements Fellegi-Sunter with unsupervised EM parameter estimation, supports PostgreSQL as a backend (which Lumogis already uses), deduplicates 7 million records in approximately 2 minutes on a laptop, and requires no labeled training data. It provides Jaro-Winkler/Levenshtein fuzzy matching, term frequency adjustments, waterfall charts for explainability, and model save/reload for incremental use.

**dedupe** (Python) is a strong alternative when active learning is desired — it asks the user to label a small number of pairs and generalises. This fits the single-user model where the user can confirm or deny matches, but requires interactive training sessions. **ZeroER's** GMM concept is intellectually elegant and could be implemented within a custom pipeline using scikit-learn for truly unsupervised matching without the Splink framework.

Other tools — DeepMatcher, Magellan/py_entitymatching, Zingg, entity-fishing, OpenEA — are either designed for enterprise scale, require extensive labeled data, or solve adjacent problems (entity linking to external KBs, cross-KG alignment) rather than within-KG deduplication.

---

## 3. Entity quality and noise filtering

### The noise problem is well-understood but under-addressed for personal KGs

Entity extraction from unstructured text inevitably produces noise: generic terms mistakenly elevated to entities ("the meeting", "the project"), partial mentions, and low-confidence extractions. The literature identifies several established filtering approaches, but **direct research on personal KG noise filtering is sparse** — most work targets enterprise or web-scale KGs.

**Ezzabady and Benamara (GenAIK 2025, ACL)** provide the most relevant recent analysis, finding that **78% of noun phrase heads in Wikipedia text are non-named entities** — phrases like "decision list" or "very good questions" that are syntactic subjects but not proper entities. Their taxonomy of extraction errors (entities containing verbs, excessive adjectives, pronouns, determiners) can be implemented as lightweight rule-based filters without the LLM they propose for correction.

Established filtering methods fall into three categories. **Rule-based filters** remove entities shorter than 2 characters, purely numeric tokens (unless typed as DATE/MONEY), entries on a stop-entity list, and entities with invalid POS composition. **Statistical filters** use mention frequency, TF-IDF scoring (penalising entities appearing in nearly every document), and extraction confidence thresholds. **Structural filters** use entity linking — entities that successfully link to a knowledge base entry are more likely genuine — though this conflicts with Lumogis's privacy-first design if it requires external KB access.

### Confidence scoring from spaCy is available but poorly calibrated

A practical challenge for Lumogis: **spaCy's NER model does not natively output well-calibrated confidence scores**. Community discussion spanning years (GitHub issues #87, #881, #5917) reveals that spaCy's beam search approach can produce probability-like scores, but these cluster at 0.0 or 1.0 in recent model versions. The `spancat` component offers a threshold parameter (default 0.5) that acts as a confidence gate, but requires training a custom model. **Zhu et al. (arXiv:2104.04318)** propose calibrated confidence estimation for NER using local and global independence assumptions, specifically designed for noisy settings.

For Lumogis, the practical implication is that relying solely on NER confidence scores is insufficient. A **composite quality score** combining multiple signals is necessary: NER confidence (when available), mention frequency weighted by recency, TF-IDF across the corpus, capitalisation pattern (mid-sentence capitalisation strongly signals a proper noun), POS tag composition, and determiner presence.

### Personal KGs should favour recall over precision — but not naively

**Balog and Kenter (ICTIR 2019)** define personal knowledge graphs as "resources of structured information about entities personally related to its user, including the ones that might not be globally important." This is the foundational paper for PKG research and highlights a critical difference from enterprise KGs: many personally-relevant entities (your dentist, "Project Atlas", your cat's name) will not exist in any public knowledge base.

**For personal KGs, recall should generally be prioritised over precision** in entity extraction. The corpus is smaller, so each entity carries more potential significance. A missed personal contact cannot be recovered without re-processing source documents. Users can more easily correct false positives (delete a noisy entity) than discover false negatives (notice something was never extracted). However, this must be balanced: unconstrained recall at a personal KG scale of thousands of documents will still produce overwhelming noise. The practical approach is to **extract aggressively but gate on a composite quality score**, placing low-confidence entities in a "staging" tier rather than the main graph, where they can be promoted upon re-mention or explicit user confirmation.

### Practical heuristics for generic versus specific entities

Several signals reliably distinguish generic from specific entities:

- **Determiner test**: Entities preceded by "the", "a", "this", "that" are more likely generic noun phrases
- **POS composition**: Entities whose tokens are all common nouns (NN) are more likely generic than those containing proper nouns (NNP)
- **Mid-sentence capitalisation**: Strongly signals a proper noun in English
- **Stop-entity list**: A maintained blacklist of known generic phrases ("the meeting", "the project", "the team", "this document") — analogous to stopword lists in information retrieval
- **Document frequency analysis**: Entities appearing across many documents as different referents (e.g., "the client" referring to different clients) are generic
- **Length heuristic**: Very short, common-word entities (1–2 words, all lowercase) are more likely generic

A recommended filtering pipeline for Lumogis processes entities through three stages: basic rule-based filtering (cheap, fast), composite quality scoring (moderate cost), and deduplication/entity resolution (as described in Section 2). Entities failing Stage 1 are discarded; entities passing Stage 1 but scoring below a threshold in Stage 2 enter a staging tier; entities passing both stages enter the main graph.

---

## 4. Relation and edge quality

### Co-occurrence is a necessary starting point but insufficient alone

Co-occurrence — inferring a relationship between entities because they appear in the same text unit — is the simplest and most common baseline for edge creation in text-derived knowledge graphs. It is also one of the weakest. As the Neo4j documentation notes, co-occurrence means "we don't really know how they're related, but we know that they are somehow related because they appeared in the same sentence." A **CEUR-WS comparative study (2023)** using co-occurrence as an explicit baseline found that dedicated relation extraction systems like REBEL and KnowGL significantly outperformed it in producing accurate knowledge graphs.

Known failure modes include **spurious associations** (entities mentioned in the same document with no meaningful relationship), **frequency bias** (high-frequency entities producing edges with everything), **loss of relation type** (co-occurrence edges are untyped), and **window-size sensitivity**. The literature is clear on window sizes: **sentence-level co-occurrence captures syntactic/grammatical relationships** and is the most defensible baseline; **paragraph-level captures topical relationships** at lower confidence; **document-level is too noisy** for meaningful relation extraction (Levy and Goldberg, 2014).

### PMI is the standard fix for spurious co-occurrence

**Pointwise Mutual Information (PMI)** measures whether two entities co-occur more than expected under independence: `PMI(x,y) = log₂(P(x,y) / (P(x)·P(y)))`. Positive PMI indicates genuine association; negative or zero PMI indicates coincidental co-occurrence. **PPMI (Positive PMI)** replaces negative values with zero, as negative PMI "tends to be unreliable unless corpora are enormous" (Jurafsky and Martin, *SLP3*). PMI has a known bias toward low-frequency events, addressable with a smoothing exponent α=0.75 (Levy et al., 2015). **Corpus-level significant PMI (cPMI)** by Damani (2013) uses Hoeffding's inequality to further filter statistically robust associations. For Lumogis, computing PPMI on sentence-level co-occurrence counts and filtering edges below a threshold (e.g., PPMI > 1.0) would dramatically reduce spurious edges.

### Edge scoring should combine multiple quality signals

A robust edge quality score for Lumogis should combine:

- **Statistical significance**: PPMI or log-likelihood ratio, filtering edges below threshold
- **Frequency**: Normalised count of observations across documents
- **Extraction confidence**: Confidence score from the relation extraction model (REBEL outputs confidence scores; OpenIE outputs 0–1 scores)
- **Temporal decay**: Half-life model with configurable per-edge-type decay rates
- **Provenance quality**: Edges derived from formal documents weighted higher than casual chat transcripts

This composite score can be expressed as: `edge_quality = w₁·frequency + w₂·PMI + w₃·confidence + w₄·temporal_decay + w₅·provenance`. The weights should be tunable, starting with equal weighting and adjusting based on the user's experience of edge quality.

### Temporal decay should use exponential half-life with per-type configuration

**Exponential decay** dominates the temporal KG literature because of its computational elegance: `W(t) = 0.5^(t/h)` where h is the half-life. As Cormode et al. demonstrate in "Forward Decay," exponentially decayed sums can be updated incrementally — given a new observation, multiply the previous sum by the decay factor and add the new weight. This makes it efficient for continuous ingestion.

**TimeDE** (IEEE, 2024) formalises time decay in TKGs using multivariate Hawkes processes. **TempoKGAT** (2024) combines time-decaying weights with selective neighbour aggregation. **Temporal PPR** (VLDB 2023) empirically confirms that interaction weights "approximately follow exponential distributions."

For Lumogis, the key insight is that **different edge types should have different half-lives**:

- **DISCUSSED_IN** (from meeting transcripts, conversations): Short half-life (~30 days). Meeting discussions are contextually relevant but fade quickly.
- **MENTIONS** (from documents, notes): Medium half-life (~180 days). Document references remain relevant longer.
- **RELATES_TO** (semantic relationships): Long half-life (~365 days). Conceptual relationships persist.
- **DERIVED_FROM** (provenance links): No decay or very long half-life. Provenance is structural, not time-sensitive.

Critically, repeated co-occurrence should **reinforce** the decay clock — if two entities continue to co-occur, the effective weight should reflect the ongoing relationship, not decay from the first observation.

### Typed relation extraction is feasible locally using REBEL

Moving beyond co-occurrence to typed relation extraction ("works at", "lives in", "manages") is desirable but harder. **REBEL** (Babelscape, EMNLP 2021) is the strongest open-source option: an end-to-end seq2seq model based on BART that produces typed triples, trained on 220+ Wikidata relation types, integrates with spaCy ≥3.0, and runs locally. **Stanford OpenIE** processes approximately 100 sentences per second on CPU with confidence scores. **OpenNRE** (Tsinghua NLP) supports both supervised and distantly supervised settings.

A significant challenge is that **relation extraction from conversational/informal text is substantially harder** than from formal documents. Tikhonova et al. (ACM, 2020) note that "utterances are short and noisy, there are often topic drifts, and a lot of pronouns." Imani (UBC, 2015) found that text simplification before OpenIE extraction improved relation accuracy. For Lumogis, a **tiered extraction strategy** is appropriate: use REBEL for well-formed sentences, dependency parsing for informal text, and fall back to PMI-filtered co-occurrence as a last resort with lower confidence.

---

## 5. Knowledge graph quality frameworks

### Three frameworks define the quality dimensions

The field converges on a core set of quality dimensions, established by three major frameworks. **Zaveri et al. (2016, *Semantic Web*)** — the most widely cited — define **18 quality dimensions grouped into 4 categories** (accessibility, intrinsic, contextual, representational) with 69 metrics. **Xue and Zou (2022, *IEEE TKDE*)** provide the most comprehensive recent survey, covering the full quality management lifecycle: assessment, error detection, error correction, and completion. **Wang et al. (2021, *Fundamental Research*)** define six core dimensions — accuracy, completeness, consistency, timeliness, availability, and conciseness — and investigate their correlations.

The dimensions most relevant to Lumogis are **accuracy** (are the extracted facts correct?), **completeness** (are important entities and relations present?), **consistency** (are there contradictions?), **timeliness** (are facts current?), and **conciseness** (are there duplicates?). Availability and interoperability matter less for a single-user local system.

**Seo et al. (2022, arXiv:2211.10011)** propose six structural quality metrics — relationship richness, attribute richness, inheritance richness, class instantiation, instantiated property ratio, and property instantiation — applied across Wikidata, DBpedia, YAGO, and Google's KG. Their key finding: a "good" KG defines detailed classes/properties in its ontology AND actively uses them in triples. Quality is not visible from scale alone.

### Industry giants confirm automation plus human curation is universal

**Noy et al. (2019, *CACM*)** — co-authored by practitioners from Google, Microsoft, Facebook, eBay, and IBM — is the definitive industry reference. Their shared challenges include entity disambiguation (still a top problem), managing consistency on evolving graphs, extraction from heterogeneous sources, and knowledge evolution. The universal pattern across all five companies: **automation combined with human oversight**. No company relies fully on automated extraction.

Lessons transferring to a personal KG: schema design matters enormously ("knowledge representation is a difficult skill"); incremental updates are the norm, not batch reconstruction; provenance tracking is essential for debugging; and quality metrics should be "fit for purpose" — a personal KG's quality needs differ fundamentally from a web-scale KG.

### SHACL provides constraint validation without cloud infrastructure

**SHACL (Shapes Constraint Language)**, a W3C Recommendation since 2017, validates graph data against shape constraints: cardinality (every Person must have a name), data types (dates must be valid), value ranges, and logical combinations. It supports severity levels (Violation, Warning, Info), enabling triage. While designed for RDF, the concept translates directly to property graph validation: define expected shapes for each entity type and validate incrementally on ingestion. **Rabbani et al. (VLDB 2023)** address automated shape extraction from KG data, avoiding the need to manually specify all constraints.

For Lumogis, implementing SHACL-equivalent validation means defining constraints like: Person must have a name property; Document must have a date; every entity must have at least one edge; no self-loops on MENTIONS edges. Running validation on each new batch of ingested data catches quality regressions early.

### Incremental quality maintenance outperforms batch rebuilds

**Hofer et al. (2024, *MDPI Information*)** provide the most comprehensive survey on incremental KG construction, finding that "quality problems aggravate over time with continuous data integration if not handled." Their recommendation: validate quality at each step of the pipeline, not just the final graph. **IncRML** achieves up to 315× less storage and 4.41× faster construction versus full rebuild by propagating only deltas. **DeepDive** achieves up to 112× speedup for inference updates with less than 1% accuracy loss.

The practical pattern for Lumogis: run lightweight validation on each ingestion batch (constraint checking, duplicate detection, quality scoring); run more expensive batch assessment (full graph metrics, comprehensive deduplication) weekly; and monitor a small set of quality regression indicators continuously.

### Human-in-the-loop curation for a solo user must be aggressively prioritised

The literature on HITL curation largely assumes crowdsourcing or expert teams. **Schröder et al. (CEUR-WS 2022)** specifically address personal KG construction, demonstrating a four-stage workflow (domain term extraction, ontology population, taxonomic learning, non-taxonomic learning) where users review AI suggestions through a GUI. Their finding: "with moderately spent effort, the knowledge engineer was able to create, accept, and reject many assertions that formed a meaningful personal knowledge graph."

For a solo user, the critical constraint is attention budget. Active learning principles suggest prioritising review items by **uncertainty × impact**: entities with low confidence scores AND high graph centrality should be reviewed first, as errors in highly-connected entities propagate widely. **CleanGraph** (Bikaun et al., 2024) provides an interactive web-based tool for KG refinement with CRUD operations and plugin ML models. **ExtracTable** demonstrates that well-designed curation interfaces reduce time from hours to approximately 25 minutes per review session.

A practical weekly curation workflow for Lumogis: present the top 10 items needing attention (duplicate candidates, low-confidence high-centrality entities, constraint violations, drift flags), allow accept/reject/edit actions, and feed decisions back into the pipeline (updating alias tables, adjusting thresholds, confirming or denying merges).

---

## 6. Concept drift and temporal consistency

### Concept drift in KGs is a semantic phenomenon, not a statistical one

**Wang, Schlobach, and Klein (2011, *Journal of Web Semantics*)** provide the foundational framework, defining concept meaning along three dimensions — **intension** (defining properties), **extension** (instances), and **label** (surface forms) — and concept drift as any change in these dimensions over time. They distinguish **concept shift** (qualitative change between two time points) from **concept instability** (degree of change over a period). **Shi et al. (2025, *Transactions in GIS*)** extend this with a finer taxonomy: concept birth, drift, shift, split, merge, and retirement.

This is fundamentally different from ML concept drift (statistical distribution shift causing model degradation). In the KG context, drift is about **changes in the meaning, structure, or labelling of knowledge** — a person changes roles, an organisation is renamed, a project evolves. **Chen et al. (2021, *Web Semantics*)** bridge these two worlds by using KG embeddings to encode semantic properties of concept drift, achieving 12–35% Macro-F1 improvements.

**Concept drift in KGs is significantly less studied than in ML.** The ML community has decades of mature detection methods (DDM, ADWIN, Page-Hinkley); KG-specific drift detection has perhaps a dozen focused papers. Personal KG drift is virtually unstudied.

### Temporal knowledge graphs provide the modelling substrate

The comprehensive survey by **Cai et al. (2024, arXiv:2403.04782)** identifies four evolutionary stages: static KGs, dynamic KGs, temporal KGs (recording valid-time intervals for facts as quadruples), and event KGs. **Polleres et al. (2023, *TGDK*)** distinguish time as data (temporal validity) from time as metadata (when changes were recorded), and discrete changes (snapshots) from continuous changes (atomic additions/removals).

**Wikidata's temporal fact handling** provides a mature, proven pattern: multiple statements with the same property carry different values and different temporal qualifiers (`start_time`, `end_time`, `point_in_time`). For entity changes, deprecated rank marks superseded information without deletion. This pattern is directly implementable in Lumogis's property graph.

### Graphiti provides the most relevant reference architecture

**Graphiti/Zep** (Rasmussen et al., 2025, arXiv:2501.13956) implements the system most similar to what Lumogis needs: **bi-temporal modelling** where every edge tracks `(t_valid, t_invalid)` for real-world validity and `(t_created, t_expired)` for system recording time. Old edges are **invalidated but not deleted**, preserving full history. New data integrates incrementally without batch recomputation. The system uses semantic search to find potentially conflicting existing edges, then determines whether a true contradiction exists.

### Lightweight drift detection heuristics for Lumogis

Given the sparse literature on personal KG drift, practical detection must rely on heuristics:

- **Surface form divergence**: Flag entities where a new surface form appears differing significantly from known aliases (Jaro-Winkler distance below threshold). For example, "Head of Marketing" and "VP Marketing" for the same person.
- **Attribute conflict**: When a new extraction contradicts an existing attribute (different job title, different location), flag for review rather than auto-overwriting.
- **Temporal gap detection**: Flag entities not mentioned in recent data (configurable window) — may indicate concept retirement or name change.
- **Relationship context change**: If an entity's graph neighbourhood changes significantly (new edges appearing, old edges disappearing), flag for review.
- **Frequency anomaly**: Sudden spikes or drops in entity mention frequency may indicate drift events.

The key data structure enabling drift detection is an **entity profile with temporal metadata**: `{entity_id, canonical_name, aliases: [{name, first_seen, last_seen, count}], attributes: [{key, value, valid_from, valid_to, source, confidence}]}`. Every modification is recorded in a change log enabling retrospective analysis and undo.

**SemaDrift** (Stavropoulos et al., 2019) and **OntoDrift** (Capobianco et al., 2020) measure stability across label, intension, and extension dimensions, but are designed for formal ontology comparison, not personal KG management. Embedding-based drift detection (Verkijk et al., 2023) is promising but explicitly flagged as immature — nearest-neighbour comparison in embedding space is "conceptually problematic" and clustering approaches remain underexplored.

---

## 7. Lumogis-specific recommendations

### Which entity resolution approach fits best

**Recommended: Splink-based probabilistic matching with embedding-based blocking via Qdrant.** The architecture should process each newly extracted entity through four stages:

1. **Normalise**: Parse name components (`python-nameparser`), expand abbreviations, normalise whitespace and casing.
2. **Block**: Type-based blocking (compare only within same entity type) + Qdrant ANN search on entity embeddings (top-10 candidates) + attribute-based blocking (first 2 characters of normalised name).
3. **Score**: Splink probabilistic model using Jaro-Winkler on name components, exact match on known aliases (nickname lookup table), embedding cosine similarity, and co-occurrence context bonus. The Splink model can be trained unsupervised via EM on the existing entity corpus and saved/reloaded for incremental use.
4. **Decide**: Score above upper threshold → auto-merge; score between thresholds → flag for human review; score below lower threshold → create new entity. Thresholds should start conservative (high upper threshold to avoid false merges) and be relaxed as the user builds confidence through the review queue.

### Threshold tuning for mention_count and co_occurrence_count gates

For `mention_count` (entity inclusion threshold): **start at 1 (include all entities) but apply a composite quality score** rather than a hard frequency cutoff. A single mention of a person's full name in a formal document should enter the graph; a single mention of "the meeting" should not. The quality score (combining NER confidence, POS composition, capitalisation, determiner presence) is a better gate than raw mention count. If a hard threshold is needed for computational reasons, **mention_count ≥ 2 for Concept entities, mention_count ≥ 1 for Person/Organisation entities** balances noise reduction with recall.

For `co_occurrence_count` (edge inclusion threshold): **use PPMI > 0 as the primary filter rather than raw count**. If raw count must be used, **co_occurrence_count ≥ 2 at sentence level, ≥ 3 at paragraph level** provides a reasonable starting point. Edges below these thresholds can be stored but excluded from traversal queries by default, surfaced only when specifically queried. The literature suggests that a combination of co-occurrence ≥ 5 and similarity ≥ 0.7 maximises F1 at approximately 0.83 for general KG construction, but personal KGs operating at smaller scale should use lower thresholds to preserve recall.

### Automated quality signals for a graph health dashboard

Six metrics should be tracked continuously:

1. **Duplicate candidate count**: Number of entity pairs scoring in the ambiguous range — indicates unresolved deduplication work. Target: decreasing over time.
2. **Orphan entity percentage**: Entities with zero edges / total entities. Orphans are likely noise or indicate missing relation extraction. Target: below 10%.
3. **Mean entity completeness**: Average (filled properties / expected properties) per entity type. Defined per schema: Person should have name + at least one relation; Document should have date + title. Target: above 70%.
4. **Constraint violation count**: Number of entities failing type-specific validation rules (Person without name, Document without date, self-loops, invalid edge types). Target: zero violations on critical constraints.
5. **Ingestion quality trend**: Rolling 7-day average of (entities passing quality gate / entities extracted). A declining trend indicates extraction quality regression or changing source document characteristics.
6. **Temporal freshness distribution**: Histogram of entity last-updated timestamps. A healthy graph shows continuous updates; large clusters of stale entities indicate potential drift or inactive data streams.

### A practical human-in-the-loop curation workflow for a solo user

The workflow should respect the solo user's limited attention budget. Weekly sessions of **15–25 minutes** are sustainable over months and years.

**Daily (automated)**: Ingest new data → extract entities and relations → score quality → auto-merge high-confidence duplicates → auto-reject low-quality entities → queue ambiguous items for review.

**Weekly review session**: Open the curation dashboard showing (a) the top 5 duplicate candidate pairs ranked by impact (centrality × ambiguity score), with source context and a merge/keep-separate/skip action; (b) the top 5 flagged items: new entities with low confidence but high connectivity, constraint violations, and drift alerts. Each item shows the entity, its source context, and one-click actions. Accept/reject decisions feed back into the pipeline: accepted merges update the alias table and refine Splink thresholds; rejected merges add to a "known distinct" list preventing future flagging.

**Monthly audit** (10 minutes): Review graph-level health metrics on the dashboard. Check for anomalies: sudden entity count spikes, orphan percentage increases, completeness drops. Optionally browse a random sample of 10 recently created entities to spot-check extraction quality. Adjust thresholds if precision/recall balance has shifted.

**Quarterly review** (30 minutes): Review entities with the most aliases (potential over-merging), entities with the highest edge count (potential hubs that accumulated spurious edges), and the oldest unreviewed flagged items.

### The minimal viable quality assurance pipeline

For Lumogis to achieve acceptable quality without cloud infrastructure or heavy ML, the minimum pipeline requires five components:

1. **Entity extraction post-processor**: Rule-based filters (stop-entity list, POS validation, determiner check, length check) applied to spaCy NER output. Estimated implementation: ~200 lines of Python. No ML required.

2. **Entity resolution with Splink**: Unsupervised probabilistic matching using PostgreSQL as the backend. Estimated implementation: Splink integration + custom blocking via Qdrant queries + nickname lookup table. No labeled data required.

3. **Edge quality scoring**: PPMI computation on co-occurrence counts + exponential temporal decay with per-edge-type half-lives. Estimated implementation: ~150 lines of Python for the scoring module + a scheduled job for periodic re-scoring.

4. **Constraint validation**: A set of per-type validation rules (equivalent to SHACL shapes but implemented in Python/SQL against Postgres). Run on each ingestion batch. Estimated implementation: ~100 lines of validation logic.

5. **Review queue with feedback loop**: A simple prioritised list of items needing human attention, surfaced in the UI. Accept/reject actions write back to alias tables and threshold configuration. Estimated implementation: API endpoint + UI component + feedback storage in Postgres.

This pipeline requires **no cloud services, no GPU, no large language models, and no labeled training data**. It runs entirely on Postgres, Qdrant, and Python libraries available via pip. A local LLM for ambiguity resolution is a valuable enhancement but not part of the minimum viable pipeline.

### Open datasets and benchmarks for evaluating ER quality

Several datasets can benchmark Lumogis's entity resolution:

- **Magellan benchmark datasets** (UW-Madison): Curated entity matching datasets across domains including person names, products, and publications. Available at sites.google.com/site/anhabordeduplication/. Includes DBLP-ACM, DBLP-Scholar, Amazon-Google Products, Abt-Buy, and person-matching datasets.
- **WN18RR and FB15k-237**: Standard KG benchmarks for link prediction, useful for evaluating edge quality models.
- **OAEI (Ontology Alignment Evaluation Initiative)**: Annual benchmarks for entity alignment across knowledge graphs.
- **ER-Evaluation** (Python library): Provides end-to-end evaluation toolkit including summary statistics and error analysis for entity resolution output.
- **DI2KG (Data Integration to Knowledge Graph) challenge datasets**: Specifically designed for KG construction from heterogeneous sources.

For personal KG-specific evaluation, **no standard benchmarks exist**. Lumogis will need to build evaluation datasets from its own data — a practical approach is to periodically sample 50–100 entity pairs from the merge/flag/create decisions, have the user label them as correct or incorrect, and compute precision/recall/F1 on the ER pipeline. Over time, this creates a personalised benchmark.

---

## 8. Open questions and research gaps

**Personal KG quality is a gap in the literature.** Almost all quality frameworks target large-scale, multi-user knowledge graphs. The solo-user, incremental, local-deployment case is underexplored. Balog and Kenter (2019) set the research agenda but the community has not yet produced comprehensive solutions for quality management in this context.

**Drift versus noise discrimination remains unsolved.** When a new extraction produces a different attribute for an existing entity, is it a genuine change (drift) or an extraction error (noise)? The literature offers no established methods for this specific discrimination in KGs. Lumogis will need empirical heuristics — likely combining extraction confidence, source authority, and temporal consistency — and should err on the side of flagging for human review rather than auto-updating.

**Optimal precision/recall tradeoffs for personal KGs lack empirical validation.** The conventional wisdom that personal KGs should favour recall is logical but has not been validated through controlled experiments. The right balance likely depends on the user's domain and usage patterns and may need to be discovered empirically per deployment.

**Co-occurrence to typed relation promotion is not well-automated.** Moving from "these entities co-occur" to "this is a WORKS_WITH relationship" remains difficult without either an LLM or domain-specific training data. REBEL handles formal text well but struggles with informal notes and transcripts. This is an area where local LLMs (as they improve in capability and efficiency) will provide the most value.

**spaCy confidence calibration is a practical blocker.** The lack of well-calibrated confidence scores from spaCy's NER pipeline forces reliance on composite heuristic scores rather than principled probabilistic thresholds. The `spancat` component is the best current option but requires custom model training.

**Embedding-based drift detection is immature.** Verkijk et al. (2023) explicitly flag that nearest-neighbour comparison in embedding space is "conceptually problematic" for concept shift detection, and clustering approaches remain underexplored. Lumogis should not invest in embedding-based drift detection at this stage.

**No universal KG quality score exists.** Despite many proposed dimensions and metrics, there is no universally accepted composite "KG quality score." Each framework proposes its own metrics. Lumogis should define a small set of quality metrics aligned with its specific use case and track these consistently rather than attempting comprehensive quality measurement.

**Link-prediction-based quality measures fail at small scale.** The LP-Measure approach explicitly states it does not work well for KGs with fewer than 100 triples, which applies to early-stage personal KGs. Quality measurement techniques designed for Wikidata or DBpedia scale do not transfer directly.

---

## 9. Key references

### Entity resolution

- Fellegi, I.P. and Sunter, A.B. (1969). "A Theory for Record Linkage." *Journal of the American Statistical Association*, 64(328), 1183–1210.
- Christen, P. (2012). *Data Matching: Concepts and Techniques for Record Linkage, Entity Resolution, and Duplicate Detection*. Springer.
- Christen, P. (2012). "A Survey of Indexing Techniques for Scalable Record Linkage and Deduplication." *IEEE TKDE*, 24(9), 1537–1555.
- Christophides, V., Efthymiou, V., Palpanas, T., Papadakis, G., and Stefanidis, K. (2020). "An Overview of End-to-End Entity Resolution for Big Data." *ACM Computing Surveys*, 53(6), Article 127.
- Papadakis, G., Ioannou, E., Thanos, E., and Palpanas, T. (2020). "Blocking and Filtering Techniques for Entity Resolution: A Survey." *ACM Computing Surveys*, 53(2).
- Li, Y., Li, J., Suhara, Y., Doan, A., and Tan, W.-C. (2020). "Deep Entity Matching with Pre-Trained Language Models" (Ditto). *PVLDB*, 14(1), 50–60.
- Wu, R., Chaba, S., Sawlani, S., Chu, X., and Thirumuruganathan, S. (2020). "ZeroER: Entity Resolution using Zero Labeled Examples." *SIGMOD 2020*.
- Mudgal, S. et al. (2018). "Deep Learning for Entity Matching." *SIGMOD 2018*.
- Kejriwal, M. (2023). "Named Entity Resolution in Personal Knowledge Graphs." arXiv:2307.12173.

### Entity quality and noise filtering

- Balog, K. and Kenter, T. (2019). "Personal Knowledge Graphs: A Research Agenda." *ICTIR 2019*, ACM.
- Chakraborty, N. et al. (2023). "A Comprehensive Survey of Personal Knowledge Graphs." *WIREs Data Mining and Knowledge Discovery*.
- Paulheim, H. (2017). "Knowledge Graph Refinement: A Survey of Approaches and Evaluation Methods." *Semantic Web*, 8(3), 489–508.
- Ezzabady, Z. and Benamara, F. (2025). "Non-Named Entity Problem in Knowledge Graph Construction." *GenAIK 2025, ACL Anthology*.
- Zhu, Y. et al. (2021). "Noisy-Labeled NER with Confidence Estimation." arXiv:2104.04318.
- Ferrández, O. et al. (2006). "Improving NER through Post-Processing Rules." *NLDB 2006*, Springer LNCS 3999.

### Relation and edge quality

- Levy, O. and Goldberg, Y. (2014). "Dependency-Based Word Embeddings." *ACL 2014*.
- Levy, O., Goldberg, Y., and Dagan, I. (2015). "Improving Distributional Similarity with Lessons Learned from Word Embeddings." *TACL*, 3, 211–225.
- Damani, O.P. (2013). "Improving Pointwise Mutual Information (PMI) by Incorporating Significant Co-occurrence." *CoNLL 2013*.
- Cormode, G. et al. "Forward Decay: A Practical Time Decay Model for Streaming Systems." DIMACS/Rutgers.
- Camacho-Collados, J., Pilehvar, M.T., and Navigli, R. (2015). "REBEL: Relation Extraction by End-to-end Language Generation." *EMNLP 2021* (Babelscape).
- Tikhonova, M. et al. (2020). "Relation Extraction from Conversational Text." *ACM*.

### Knowledge graph quality frameworks

- Zaveri, A. et al. (2016). "Quality Assessment for Linked Data: A Survey." *Semantic Web*, 7(1), 63–93.
- Xue, B. and Zou, L. (2022). "Knowledge Graph Quality Management: A Comprehensive Survey." *IEEE TKDE*, 35(5).
- Wang, Q. et al. (2021). "Knowledge Graph Quality Control: A Survey." *Fundamental Research*.
- Noy, N. et al. (2019). "Industry-Scale Knowledge Graphs: Lessons and Challenges." *Communications of the ACM*, 62(8), 36–43.
- Seo, S. et al. (2022). "Structural Quality Metrics to Evaluate Knowledge Graphs." arXiv:2211.10011.
- Hofer, M. et al. (2024). "Construction of Knowledge Graphs: Current State and Challenges." *MDPI Information*, 15(8).
- Schröder, M. et al. (2022). "A Human-in-the-Loop Approach for Personal Knowledge Graph Construction." *CEUR-WS*.
- Rabbani, K. et al. (2023). "Extraction of Validating Shapes from Very Large Knowledge Graphs." *VLDB 2023*.

### Concept drift and temporal consistency

- Wang, S., Schlobach, S., and Klein, M. (2011). "Concept Drift and How to Identify It." *Journal of Web Semantics*, 9(3), 247–265.
- Shi, Y. et al. (2025). "Defining Concept Drift and Its Variants in Research Data Management." *Transactions in GIS*.
- Stavropoulos, T.G. et al. (2019). "SemaDrift: A Hybrid Method and Visual Tools to Measure Semantic Drift in Ontologies." *Journal of Web Semantics*, 54, 87–106.
- Polleres, A. et al. (2023). "How Does Knowledge Evolve in Open Knowledge Graphs?" *TGDK*, 1(1), 11:1–11:59.
- Cai, L. et al. (2024). "A Survey on Temporal Knowledge Graph: Representation Learning and Applications." arXiv:2403.04782.
- Rasmussen, P. et al. (2025). "Zep: A Temporal Knowledge Graph Architecture for Agent Memory." arXiv:2501.13956.
- Chen, J., Lécué, F., Pan, J.Z. et al. (2021). "Knowledge Graph Embeddings for Dealing with Concept Drift in Machine Learning." *Web Semantics*, 67, 100625.
- Verkijk, S. et al. (2023). "Do You Catch My Drift? On the Usage of Embedding Methods to Measure Concept Shift in Knowledge Graphs." *ACM DL / ISWC Workshop*.
- Skjæveland, M.G., Balog, K. et al. (2024). "An Ecosystem for Personal Knowledge Graphs: A Survey and Research Roadmap." *AI Open*, 5, 55–69.