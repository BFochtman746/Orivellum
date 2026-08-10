**WRITING_ARCHITECT**

**FORENSIC RESEARCH TEARDOWN\
AND EXECUTABLE BOOK-WRITING IMPLEMENTATION SPECIFICATION**

*Archive-grounded analysis of WRITING_ARCHITECT.zip\
Deep research baseline • August 2026*

  -----------------------------------------------------------------------
  **DECISION\
  **Do not add another "master prompt." Rebuild the archive as a
  governed, testable Book Production Operating System. Preserve the
  strongest intellectual assets, retire duplicated authorities, and make
  every stage executable through structured records, lifecycle gates,
  evidence, and human approval.
  -----------------------------------------------------------------------

  -----------------------------------------------------------------------

Prepared for Brian Fochtman

# Executive Decision

The archive contains valuable and unusually ambitious writing doctrine,
but it is not yet a production system. It is a collection of overlapping
documents, prompt frameworks, scoring systems, release packages, and
nested repositories with no single machine-enforceable authority model.
Its strongest concepts---source authority, hard gates, manuscript
stabilization, voice protection, atomic quality checks, continuity
control, provenance, and reader validation---should be retained. Its
weakest pattern---repeated creation of "ultimate," "final," "master,"
and "complete" documents---must stop.

A flawless book cannot be guaranteed by any AI or editorial system.
"Flawless" must be redefined as a controlled release claim: no known
blocking defect remains, every required review has evidence, every
factual claim has traceable support, every continuity conflict is
resolved or waived, the manuscript passes defined human and machine
gates, and an accountable human author approves the release. The system
can make defects far less likely and much easier to detect; it cannot
make literary value mathematically infallible.

  -----------------------------------------------------------------------
  **Recommended build order\
  **1) establish authority and inventory; 2) normalize the book data
  model; 3) implement research and evidence first; 4) implement chapter
  contracts and continuity; 5) implement bounded drafting; 6) implement
  independent editorial passes; 7) qualify the system against fixed
  benchmark manuscripts; 8) only then enable continuous autonomous work.
  -----------------------------------------------------------------------

  -----------------------------------------------------------------------

# Forensic Scope and Method

This report analyzes the supplied WRITING_ARCHITECT archive recursively,
including nested ZIP packages. It evaluates research architecture and
implementation readiness rather than judging the artistic merit of the
user's books. The method combined file-system forensics, package
expansion, cryptographic duplicate analysis, document-content
extraction, architecture comparison, and current online research into
professional publishing and long-form AI writing.

  -----------------------------------------------------------------------
  **Measure**                         **Observed result**
  ----------------------------------- -----------------------------------
  Initial archive entries             412

  Recursively expanded files          852

  Non-metadata records analyzed       485

  DOCX files analyzed                 322

  Extracted textual words             247,818

  Exact duplicate groups              164

  Files participating in exact        331
  duplicate groups                    

  Distinct SHA-256 payloads           318

  Nested ZIP packages expanded        5
  -----------------------------------------------------------------------

The duplicate count is not merely cosmetic. It means the archive cannot
currently answer a foundational governance question: which physical copy
is authoritative, and which copies are generated mirrors, stale
releases, embedded dependencies, or accidental duplication?

# 1. Forensic Findings

## 1.1 Authority collapse

The archive uses authority-bearing labels repeatedly: MASTER, FINAL,
COMPLETE, HARDENED, DOMINANT, ULTIMATE, LOCK, and RC. These labels
appear across independent systems without a shared release registry or
supersession chain. A human reader can infer that NarrativeOS v24.4
probably supersedes v24.1, but software cannot safely infer that every
later number supersedes every earlier component. Some older files may
remain dependencies; some later files may only extend a layer.

-   No canonical manifest declares the one accepted version of each
    system capability.

-   No object-level supersession records distinguish replacement,
    extension, deprecation, or retained historical evidence.

-   No acceptance signature proves that a "final" file passed defined
    release gates.

-   Nested packages reproduce material, creating parallel authorities.

## 1.2 Prompt-rich, implementation-poor

Many documents are sophisticated operating instructions for a model, but
they remain prose protocols. They do not define executable schemas,
typed interfaces, persistent workflow state, deterministic validators,
or automated test fixtures. A prompt can tell an agent not to drift, but
only software can prevent a forbidden state transition, require an
evidence link, or block promotion when a gate is unresolved.

  -----------------------------------------------------------------------
  **Current pattern**                 **Required conversion**
  ----------------------------------- -----------------------------------
  Narrative rule in prose             Machine-readable policy with rule
                                      ID and validator

  Chapter checklist                   ChapterContract schema plus
                                      pass/fail evidence

  Quality score                       Versioned rubric, atomic
                                      observations, calibration set,
                                      confidence

  Book bible document                 Normalized canon entities and
                                      relationships

  Research prompt                     ResearchQuestion, Source, Claim,
                                      Evidence, Conflict records

  Final manuscript file               Immutable release artifact linked
                                      to source state and approvals

  Agent persona                       Worker contract with allowed tools,
                                      budget, outputs, stop conditions
  -----------------------------------------------------------------------

## 1.3 Strong internal assets

NarrativeOS --- Best source for lifecycle gates, source-file authority,
input stabilization, conflict hierarchy, deployment controls, and
refusal to progress under weak inputs.

FORGE v3.3 --- Best source for atomic literary evaluation, dimension
decomposition, scoring ceilings, and calibrated review discipline.

Ultimate Prose System --- Best broad operational QA framework, but too
large and partially redundant; should become a library of evaluators
rather than the governing spine.

Unified Writers Room --- Best source for role separation, context
isolation, output contracts, and independent reader-review passes.

Voice Architect and Author Profile --- Best source for author-specific
style constraints and measurable voice fingerprints; must protect rather
than homogenize the author.

Book Bible / world and character systems --- Useful conceptual source
for canon, continuity, character, location, chronology, motif, and
subplot data models.

Biblical Research prompt --- Useful intent, but materially insufficient
as a scholarly protocol because it lacks source hierarchy, edition
control, uncertainty representation, and claim-level evidence.

AI Provenance systems --- Useful for recording process provenance, but
unreliable if treated as a detector that can conclusively determine AI
authorship from prose alone.

## 1.4 Core failure modes

  -----------------------------------------------------------------------
  **ID**            **Failure mode**  **Effect**        **Severity**
  ----------------- ----------------- ----------------- -----------------
  FM-01             Authority         Wrong file        Critical
                    ambiguity         becomes source    
                                      truth             

  FM-02             Duplicate         Two copies evolve Critical
                    divergence        independently     

  FM-03             Prompt-only       Rules are ignored Critical
                    enforcement       or inconsistently 
                                      interpreted       

  FM-04             Rubric            Aesthetic scores  High
                    overprecision     imply false       
                                      measurement       
                                      certainty         

  FM-05             Evaluator         Drafting model    High
                    contamination     grades its own    
                                      work              

  FM-06             Context overload  Too many systems  High
                                      loaded together   
                                      degrade attention 

  FM-07             Research          Unsupported facts Critical
                    hallucination     enter narrative   
                                      canon             

  FM-08             Voice flattening  Repeated          High
                                      optimization      
                                      removes authorial 
                                      distinctiveness   

  FM-09             Premature         Line edits hide   High
                    polishing         structural        
                                      defects           

  FM-10             Version bloat     Every improvement High
                                      creates another   
                                      master document   

  FM-11             No benchmark      Claims of quality Critical
                    corpus            have no           
                                      calibrated        
                                      baseline          

  FM-12             No release        "Final" is a      Critical
                    evidence          label, not a      
                                      verified state    
  -----------------------------------------------------------------------

# 2. What Current Research Changes

## 2.1 Long-form generation requires hierarchical and adaptive planning

Current research converges on a key point: local fluency is not
document-level coherence. Long-form systems need hierarchical planning,
chapter- or event-level decomposition, dynamic retrieval, and revision.
Dynamic hierarchical expansion, recursive heterogeneous planning, and
multi-agent outline/planning/writing designs all target the same
deficiency: a model can write a good paragraph while losing global
causality, chronology, coverage, or thematic progression.

Implementation consequence: the system must never ask one model call to
"write the book." It must maintain a persistent plan tree whose nodes
are requirements, sections, scenes, claims, and unresolved decisions.
Each node needs completion state and evidence.

## 2.2 Reflection helps, but self-review is not independent verification

Reflection-driven systems improve quality by separating plan, write,
critique, and revise. That supports the archive's writers-room concept.
However, the same model family can share blind spots across roles.
Critical factual, theological, continuity, and release decisions require
deterministic checks, source retrieval, alternate evaluators, and human
approval---not merely a differently named persona.

## 2.3 Two-stage evidence selection improves faithfulness

Research on long-document summarization shows that selecting source
highlights before generating prose improves traceability and factual
consistency. This maps directly to book research: establish an approved
evidence packet before drafting a factual passage. The drafted text must
cite which evidence units it used, even when the final published prose
does not expose citations inline.

## 2.4 Professional publishing is staged and role-separated

Professional publishing separates editorial development, managing
editorial, copyediting, design/coding, proofing, and production. Penguin
Random House describes managing editorial as the traffic-control center
and documents manuscript-to-production steps that start after editorial
review. The system should mirror that separation rather than collapse
structure, prose, copyediting, and formatting into one pass.

## 2.5 AI use requires provenance and disclosure controls

Publisher policies increasingly require disclosure of approved AI use.
Therefore provenance is not an optional forensic add-on. Every generated
or substantially revised passage should record model, prompt/policy
version, source packet, human edits, and approval history. Provenance
should describe process; it should not claim scientifically certain AI
detection from prose alone.

# 3. Target System: Book Production Operating System

The corrected architecture is a stateful production system, not a
collection of prompts. The user opens a book project and sees a governed
view of purpose, source authority, research, structure, drafting,
continuity, editorial findings, release readiness, and next action.

## 3.1 Architectural layers

  -----------------------------------------------------------------------
  **Layer**                           **Responsibilities**
  ----------------------------------- -----------------------------------
  Experience                          Chat, book dashboard, research map,
                                      manuscript view, findings,
                                      approvals, status

  Book domain service                 Books, editions, chapters, scenes,
                                      canon, chronology, claims, evidence

  Workflow engine                     Persistent stages, gates, retries,
                                      assignments, approvals, rollback

  Worker services                     Research, architecture, drafting,
                                      developmental review, continuity,
                                      fact check, line edit, copyedit

  Retrieval and graph                 Exact + semantic retrieval, entity
                                      resolution, claim graph, temporal
                                      relationships

  Artifact service                    Immutable originals, working
                                      drafts, exports, manifests,
                                      checksums

  Policy and audit                    Authority, permissions, provenance,
                                      model/tool logs, release evidence

  Execution sandbox                   Isolated code/document tooling; no
                                      direct overwrite of authority
  -----------------------------------------------------------------------

## 3.2 Canonical lifecycle

  -----------------------------------------------------------------------
  **State**               **Name**                **Exit condition**
  ----------------------- ----------------------- -----------------------
  B0                      INTAKE                  Files received, hashed,
                                                  malware-scanned,
                                                  metadata captured

  B1                      AUTHORITY_RESOLUTION    Versions compared;
                                                  authoritative and
                                                  historical artifacts
                                                  designated

  B2                      BOOK_DEFINITION         Reader promise, genre,
                                                  audience,
                                                  thesis/premise,
                                                  constraints approved

  B3                      RESEARCH_BASELINE       Questions, sources,
                                                  claims, conflicts, and
                                                  gaps established

  B4                      ARCHITECTURE            Master structure and
                                                  chapter contracts
                                                  approved

  B5                      DRAFTING                Sections drafted only
                                                  from approved contract
                                                  and evidence packet

  B6                      DEVELOPMENTAL_EDIT      Structure, causality,
                                                  character, argument,
                                                  pacing repaired

  B7                      VERIFICATION            Factual, citation,
                                                  theology/domain,
                                                  chronology, continuity
                                                  gates

  B8                      LINE_EDIT               Voice, clarity, rhythm,
                                                  diction, paragraph
                                                  movement

  B9                      COPYEDIT                Grammar, usage,
                                                  consistency, style
                                                  sheet, cross-references

  B10                     PRODUCTION              Layout, front/back
                                                  matter, accessibility,
                                                  print/eBook outputs

  B11                     PROOF                   Rendered proof and
                                                  final defect closure

  B12                     RELEASE_CANDIDATE       All evidence assembled;
                                                  no open blocker

  B13                     RELEASED                Author-approved
                                                  immutable release with
                                                  manifest and rollback
                                                  source
  -----------------------------------------------------------------------

  -----------------------------------------------------------------------
  **Hard rule\
  **A downstream pass cannot compensate for an upstream failure. A
  beautiful line edit cannot promote a structurally broken chapter. A
  clean proof cannot cure unsupported claims. Any blocking upstream
  defect returns the affected scope to the proper lifecycle state.
  -----------------------------------------------------------------------

  -----------------------------------------------------------------------

# 4. Canonical Data Model

  -----------------------------------------------------------------------
  **Object**                          **Required purpose**
  ----------------------------------- -----------------------------------
  BookProject                         Stable identity, author, title,
                                      form, audience, reader promise,
                                      scope, status

  Edition                             Book version lineage, branch,
                                      release target, authority status

  SourceArtifact                      Original file, hash, origin,
                                      rights, extraction status,
                                      authority

  ResearchQuestion                    Question, scope, priority,
                                      sufficiency criteria, state

  Source                              Bibliographic identity, edition,
                                      date, reliability, rights,
                                      retrieval record

  Claim                               Atomic proposition, type,
                                      confidence, temporal validity

  EvidenceUnit                        Exact passage/data supporting or
                                      challenging a claim

  Conflict                            Competing claims, reason,
                                      adjudication, unresolved risk

  CanonEntity                         Person, place, object, institution,
                                      concept, relationship

  CanonFact                           Time-bounded fact about an entity
                                      with evidence

  TimelineEvent                       Date range, participants, causes,
                                      effects, uncertainty

  NarrativeArc                        Initial condition, changes, turning
                                      points, resolution

  ChapterContract                     Purpose, reader outcome, required
                                      content, evidence, dependencies,
                                      gates

  SceneContract                       Goal, conflict, turn, outcome, POV,
                                      location, time, knowledge state

  DraftUnit                           Versioned prose linked to contract
                                      and evidence packet

  EditorialFinding                    Type, severity, location, evidence,
                                      proposed resolution, state

  StyleRule                           Author/project style constraint
                                      with positive and negative examples

  EvaluationObservation               Atomic check result, evidence span,
                                      confidence, evaluator

  Approval                            Decision, authority, object, scope,
                                      timestamp, conditions

  ReleaseCandidate                    Frozen composition of approved
                                      artifacts and evidence

  ReleaseManifest                     Hashes, versions, tools, models,
                                      approvals, test results
  -----------------------------------------------------------------------

# 5. Research System Implementation

## 5.1 Source hierarchy

For biblical historical fiction, the system must distinguish textual
authority from historical reconstruction and creative interpolation. The
current "Ultimate Biblical Research" prompt blends these categories and
uses language such as "flawless" and "absolute chronological timeline,"
which is epistemically unsafe where manuscripts, chronology,
archaeology, and interpretation legitimately disagree.

  -----------------------------------------------------------------------
  **Tier**                **Source class**        **Permitted use**
  ----------------------- ----------------------- -----------------------
  T1                      Primary biblical text   Scriptural baseline;
                          with named              variants recorded
                          translation/edition and 
                          original-language       
                          witness                 

  T2                      Critical editions,      Textual/historical
                          textual apparatus,      evidence
                          primary inscriptions,   
                          archaeological reports  

  T3                      Peer-reviewed           Interpretation and
                          scholarship and         synthesis
                          academic monographs     

  T4                      Credible reference      Orientation and
                          works and               corroboration
                          museum/university       
                          resources               

  T5                      Confessional commentary Theological
                          and theological         interpretation;
                          tradition               tradition labeled

  T6                      Popular works, blogs,   Lead generation only;
                          unsourced summaries     not sufficient evidence

  T7                      AI-generated statements Never evidence; must
                                                  resolve to an external
                                                  source
  -----------------------------------------------------------------------

## 5.2 Research workflow

1.  Define the narrative question and the exact decision it informs.

2.  Search existing internal sources before external acquisition.

3.  Create candidate claims without accepting them into canon.

4.  Acquire sources and capture full bibliographic identity, edition,
    date, and rights.

5.  Extract evidence units with exact page/location references.

6.  Link each claim to supporting, qualifying, and contradicting
    evidence.

7.  Represent uncertainty explicitly: confirmed, probable, possible,
    disputed, unknown, or invented-for-fiction.

8.  Run source-diversity and independence checks.

9.  Have a separate verifier confirm that evidence supports the claim.

10. Promote only approved claims into canon or chapter evidence packets.

## 5.3 Claim acceptance gate

  -----------------------------------------------------------------------
  **Check**                           **Pass condition**
  ----------------------------------- -----------------------------------
  Identity                            Claim has stable ID and exact
                                      wording

  Evidence                            At least one admissible evidence
                                      unit; higher-risk claims require
                                      independent corroboration

  Edition control                     Textual quotations identify
                                      translation/edition/witness

  Temporal validity                   Dates or historical period are
                                      represented

  Conflict                            Known disagreement is recorded and
                                      not hidden

  Narrative use                       Fact, interpretation, tradition, or
                                      creative interpolation is labeled

  Rights                              Quotation and image use is allowed

  Verifier                            Independent review completed

  Approval                            Canon authority accepts or rejects
  -----------------------------------------------------------------------

# 6. Planning and Chapter Contracts

The chapter contract is the central execution object. It prevents the
model from improvising requirements, research, continuity, and voice
simultaneously. NarrativeOS supplies hard-gate thinking; current
long-form research supplies hierarchical decomposition; professional
publishing supplies role separation.

  -----------------------------------------------------------------------
  **Field**                           **Required content**
  ----------------------------------- -----------------------------------
  Contract ID                         Stable ID and version

  Purpose                             Why this chapter exists

  Reader state change                 What the reader should know, feel,
                                      believe, or anticipate afterward

  Structural role                     Opening, escalation, reversal,
                                      revelation, aftermath, synthesis,
                                      etc.

  Required beats/claims               Content that must appear

  Forbidden content                   Spoilers, contradictions,
                                      unsupported claims, premature
                                      revelations

  POV/knowledge state                 What the viewpoint character knows
                                      and cannot know

  Time/location                       Canonical coordinates and
                                      uncertainty

  Evidence packet                     Approved evidence units usable in
                                      prose

  Voice profile                       Applicable style rules and examples

  Dependencies                        Earlier promises, later payoffs,
                                      arc obligations

  Target range                        Word-count range as a planning
                                      constraint, not a quality metric

  Acceptance tests                    Specific pass/fail checks

  Open decisions                      Items requiring author choice
  -----------------------------------------------------------------------

## 6.1 Plan tree

Store planning as a tree: Book Promise → Parts → Chapters →
Scenes/Sections → Beats/Claims. Every node carries purpose,
dependencies, state, and evidence. The system may recursively decompose
a node, but it may not silently change an approved ancestor. Proposed
structural changes become explicit change requests with impact analysis.

# 7. Drafting Engine

## 7.1 Worker input contract

-   One approved chapter or scene contract

-   Relevant canon slice, not the entire vault

-   Approved evidence packet

-   Author voice profile with examples and prohibited tendencies

-   Prior and next unit summaries for continuity

-   Open editorial constraints

-   Output schema requiring prose plus provenance map

## 7.2 Bounded generation loop

11. Generate a scene/section intent summary.

12. Check intent against the contract before prose generation.

13. Draft one bounded unit.

14. Run deterministic checks: required names, dates, forbidden facts,
    length range, quotation integrity.

15. Run a critic pass that returns findings, not rewritten prose.

16. Revise only accepted findings.

17. Compare revision against voice drift and factual changes.

18. Stop after the configured revision budget and escalate unresolved
    blockers.

## 7.3 Anti-drift controls

  -----------------------------------------------------------------------
  **Control**                         **Implementation**
  ----------------------------------- -----------------------------------
  Canon retrieval                     Query by active entities, time,
                                      location, and knowledge state

  Continuity ledger                   Record every new fact introduced by
                                      prose

  Promise/payoff ledger               Track setup, reinforcement,
                                      fulfillment, and abandonment

  Character knowledge matrix          Prevent characters knowing future
                                      or offstage information

  Voice fingerprint                   Measure sentence distribution,
                                      openings, sensory balance, metaphor
                                      domains, dialogue habits

  Change budget                       Limit scope of each revision and
                                      report semantic changes

  Evidence lock                       Factual prose cannot introduce
                                      unsupported claim IDs

  Author veto                         No passage becomes authority
                                      without author acceptance
  -----------------------------------------------------------------------

# 8. Editorial and Verification Architecture

  -----------------------------------------------------------------------
  **Pass**                            **Primary scope**
  ----------------------------------- -----------------------------------
  Developmental                       Book/chapter purpose, structure,
                                      causality, argument, arc, pacing,
                                      redundancy

  Continuity                          Timeline, geography, character
                                      knowledge, names, objects,
                                      injuries, promises, theology/canon

  Factual                             Every material factual claim maps
                                      to admissible evidence

  Sensitivity/domain                  Qualified human review where
                                      subject requires expertise

  Line                                Voice, clarity, rhythm, diction,
                                      imagery, paragraph movement

  Copy                                Grammar, usage, consistency, style
                                      sheet, citations, cross-references

  Proof                               Rendered artifact, typography,
                                      widows/orphans, headings, page
                                      references, missing text
  -----------------------------------------------------------------------

## 8.1 Independence rules

-   The drafting worker cannot close its own blocking findings.

-   A reviewer returns findings with evidence; rewriting is a separate
    action.

-   A score never overrides a blocking defect.

-   All subjective scores include confidence and evaluator identity.

-   Human literary judgment remains authoritative for voice, meaning,
    theology, and release.

## 8.2 FORGE conversion

FORGE's 18 dimensions should not remain a single composite grade.
Convert each dimension into atomic observations with evidence spans.
Preserve ceilings as release constraints, but calibrate thresholds using
a benchmark corpus and inter-rater agreement. Report distributions and
unresolved findings rather than claiming a manuscript is objectively
"9.8."

# 9. Quality Model: Replace "Flawless" With Release Assurance

## 9.1 Defect severity

  -----------------------------------------------------------------------
  **Severity**            **Definition**          **Release effect**
  ----------------------- ----------------------- -----------------------
  Blocker                 Contradicts authority,  Cannot release
                          unsupported material    
                          fact, missing required  
                          chapter function,       
                          corrupt artifact,       
                          rights violation        

  Critical                Major structural,       Cannot release without
                          continuity,             explicit waiver
                          theological/domain, or  
                          reader-comprehension    
                          failure                 

  Major                   Material weakness       Must resolve or
                          affecting quality or    document waiver
                          consistency             

  Minor                   Localized defect with   May defer with recorded
                          limited reader impact   rationale

  Observation             Preference or optional  No release block
                          improvement             
  -----------------------------------------------------------------------

## 9.2 Release gates

19. All required lifecycle stages complete.

20. Zero open blockers and critical defects.

21. Every material claim has admissible evidence or is labeled creative
    interpolation.

22. Continuity checks pass across the full manuscript.

23. Author voice-drift thresholds pass or are accepted by the author.

24. Independent human read completed for the intended reader profile.

25. Copyedit and proof defects are closed.

26. DOCX/PDF/eBook outputs render and validate.

27. Release manifest, hashes, provenance, and rollback source exist.

28. Author signs the release acceptance decision.

## 9.3 Completion scoring

Completion must be evidence-based and multidimensional. Suggested
weights are configurable, but a blocked gate remains blocked regardless
of average score.

  -----------------------------------------------------------------------
  **Dimension**           **Weight**              **Evidence**
  ----------------------- ----------------------- -----------------------
  Authority and inventory 10%                     All sources classified
                                                  and authoritative
                                                  lineage resolved

  Research                15%                     Question coverage,
                                                  source quality,
                                                  conflict closure

  Architecture            15%                     Approved plan tree and
                                                  chapter contracts

  Draft                   20%                     Required units accepted

  Developmental closure   15%                     Findings closed

  Verification            10%                     Facts, citations,
                                                  continuity, domain
                                                  review

  Line/copy edit          8%                      Style and copy findings
                                                  closed

  Production/proof        7%                      Outputs rendered,
                                                  validated, and accepted
  -----------------------------------------------------------------------

# 10. Provenance and Authorship

The archive's provenance work should be redirected from definitive "AI
detection" toward verifiable process provenance. Current AI-text
detectors are vulnerable to false positives, paraphrasing, model drift,
and domain effects. The defensible record is not "this sentence is 82%
AI." It is "this passage originated in draft run X, using model Y,
prompt/policy Z, evidence packet E, then received these human edits and
approvals."

  -----------------------------------------------------------------------
  **Record**                          **Required fields**
  ----------------------------------- -----------------------------------
  GenerationEvent                     Model/version, parameters, worker,
                                      prompt hash, time, input object IDs

  EvidenceUse                         Evidence IDs retrieved and cited by
                                      the generation

  HumanEdit                           Editor, before/after diff,
                                      rationale, time

  ReviewEvent                         Reviewer, rubric version, findings,
                                      confidence

  ApprovalEvent                       Approver, scope, decision,
                                      conditions

  ReleaseLineage                      All source versions composing the
                                      released artifact
  -----------------------------------------------------------------------

# 11. Technical Implementation Specification

## 11.1 Recommended stack

  -----------------------------------------------------------------------
  **Component**           **Recommended starting  **Reason**
                          choice**                
  ----------------------- ----------------------- -----------------------
  Authoritative metadata  PostgreSQL              Transactions, schemas,
                                                  constraints,
                                                  auditability

  Object/artifact storage Local S3-compatible     Immutable originals and
                          storage or governed     large artifacts
                          filesystem              

  Search                  PostgreSQL full-text +  Exact names and
                          vector or Qdrant hybrid semantic concepts
                          retrieval               

  Knowledge graph         PostgreSQL relations    Avoid premature
                          initially               graph-database
                                                  complexity

  Workflow                LangGraph persistence   Durability and
                          initially; Temporal for resumability
                          mature long-running     
                          production              

  Version control         Git for structured      Diff, branching,
                          text, prompts, schemas, rollback
                          code                    

  Document generation     DOCX pipeline with      Professional
                          styles, comments,       author/editor exchange
                          tracked-change support, 
                          render QA               

  Model service           Local OpenAI-compatible Sovereignty and
                          endpoints plus explicit capability routing
                          cloud escalation        

  UI                      Book/project dashboard  User sees knowledge and
                          integrated with chat    state, not folders
  -----------------------------------------------------------------------

## 11.2 API boundaries

  -----------------------------------------------------------------------
  **Endpoint/service**                **Contract**
  ----------------------------------- -----------------------------------
  POST /books                         Create governed BookProject

  POST /artifacts/intake              Hash, store, extract, classify
                                      source

  POST /authority/resolve             Propose and approve authoritative
                                      lineage

  POST /research/questions            Create research question and
                                      sufficiency criteria

  POST /claims                        Create candidate claim; no
                                      automatic canon promotion

  POST /chapter-contracts             Create/version/approve chapter
                                      contract

  POST /draft-runs                    Execute bounded draft worker

  POST /findings                      Create editorial finding with
                                      evidence

  POST /transitions                   Request lifecycle transition;
                                      policy enforced

  POST /release-candidates            Freeze candidate and run gates

  POST /releases                      Author-approved promotion only
  -----------------------------------------------------------------------

## 11.3 Minimum database constraints

-   No DraftUnit without an approved ChapterContract.

-   No factual Claim accepted without at least one EvidenceUnit.

-   No ReleaseCandidate with an open blocker.

-   No lifecycle transition without authorized actor and transition
    record.

-   No mutable overwrite of a released artifact.

-   No source quotation without edition/location metadata.

-   No worker may approve its own blocking finding.

-   No authority designation without supersession rationale.

# 12. Migration of Existing Archive

## 12.1 Preserve, do not merge blindly

Create a read-only evidence snapshot of the supplied ZIP and record its
SHA-256. Do not rewrite originals. Build a migration catalog that
assigns every file one status: canonical candidate, supporting doctrine,
implementation artifact, historical version, generated duplicate, exact
duplicate, packaging metadata, or rejected/obsolete.

## 12.2 Capability consolidation map

  -------------------------------------------------------------------------------
  **Canonical       **Primary         **Secondary       **Disposition**
  capability**      source**          inputs**          
  ----------------- ----------------- ----------------- -------------------------
  Lifecycle and     NarrativeOS v24.4 v24.1, v24.3, UMS Normalize into policies
  gates                                                 and workflow states

  Evaluation        FORGE v3.3        Ultimate Prose    Convert to atomic
                                      System,           observations and
                                      Diagnostic        calibrated rubrics

  Worker            Unified Writers   NarrativeOS       Convert personas into
  orchestration     Room              engine hierarchy  worker contracts

  Voice             Voice Architect + Held-Breath       Create versioned
                    Author Profile    Voice, prose      StyleProfile
                                      references        

  Canon             Book Bible        World Build,      Normalize into
                                      Character         entities/facts/timeline
                                      Profile, Bible    
                                      data              

  Research          Biblical Research BIBLE_DATA assets Replace prompt with
                    intent                              claim/evidence workflow

  Provenance        AI Provenance     Module 7 guide    Retain process lineage;
                    v2.0                                retire definitive
                                                        detector claims

  Release           Sovereign         NarrativeOS       Create manifests, gates,
                    repositories      deployment layers hashes, acceptance
                                                        records
  -------------------------------------------------------------------------------

## 12.3 Deletion policy

Do not delete during initial migration. First prove that each duplicate
is byte-identical, each historical version is represented in lineage,
and each nested package is reproducible. After acceptance, generated
duplicates may be removed from the active workspace while remaining
represented in the immutable source snapshot and forensic manifest.

# 13. Verification and Qualification Program

## 13.1 Benchmark corpus

Build a rights-cleared benchmark corpus containing strong, weak,
contradictory, historically uncertain, continuity-broken,
voice-drifting, and formatting-damaged samples. Include both
Brian-authored material and synthetic fixtures. Each fixture needs an
oracle: known defects, expected findings, allowed variation, and
severity.

## 13.2 Required test families

  -----------------------------------------------------------------------
  **Test family**                     **Examples**
  ----------------------------------- -----------------------------------
  Schema                              Required fields, invalid
                                      transitions, orphan evidence

  Retrieval                           Exact name recall, semantic recall,
                                      temporal filtering, adversarial
                                      irrelevant context

  Research                            Unsupported claim rejection, source
                                      conflict, edition mismatch,
                                      fabricated citation

  Continuity                          Age/date conflict, impossible
                                      travel, knowledge leak, name drift,
                                      object resurrection

  Drafting                            Contract coverage, forbidden
                                      content, evidence-only factuality,
                                      revision budget

  Voice                               Author fingerprint preservation,
                                      homogenization, repeated
                                      structures, metaphor-domain drift

  Editorial                           Known defect recall, false-positive
                                      rate, inter-rater agreement

  Document                            DOCX render, comments, tracked
                                      changes, styles, TOC, page breaks,
                                      PDF comparison

  Recovery                            Restart mid-workflow, idempotent
                                      retry, rollback, corrupted cache

  Security                            Prompt injection in source,
                                      malicious file, path traversal,
                                      secret exposure, unauthorized
                                      release
  -----------------------------------------------------------------------

## 13.3 Acceptance thresholds

-   100% recall on seeded blocker defects in the qualification corpus.

-   Zero unauthorized authority transitions or releases.

-   Zero fabricated source identifiers in accepted research output.

-   All release artifacts reproduce from manifest inputs.

-   All workflow interruptions resume without duplicate irreversible
    effects.

-   Voice review demonstrates no systematic flattening against accepted
    author samples.

-   Human reviewers agree that the dashboard exposes enough evidence to
    approve or reject every gate.

# 14. Phased Build Plan

  -----------------------------------------------------------------------
  **Release**             **Purpose**             **Exit artifact**
  ----------------------- ----------------------- -----------------------
  WR-00                   Forensic baseline       Immutable archive
                                                  snapshot, complete
                                                  manifest,
                                                  duplicate/lineage
                                                  report, authority
                                                  candidates

  WR-01                   Book domain foundation  Database schemas,
                                                  lifecycle, policies,
                                                  audit, project
                                                  dashboard

  WR-02                   Research and evidence   Source intake,
                                                  questions, claims,
                                                  evidence, conflicts,
                                                  citations

  WR-03                   Canon and continuity    Entities, facts,
                                                  timeline, knowledge
                                                  states, continuity
                                                  validators

  WR-04                   Architecture            Plan tree,
                                                  chapter/scene
                                                  contracts, impact
                                                  analysis

  WR-05                   Drafting vertical slice One bounded chapter
                                                  workflow with
                                                  provenance and approval

  WR-06                   Editorial passes        Developmental,
                                                  continuity, factual,
                                                  line, copy finding
                                                  systems

  WR-07                   Document production     DOCX branches,
                                                  comments/tracked
                                                  changes,
                                                  render-and-proof QA

  WR-08                   Benchmark and           Fixtures, oracle tests,
                          qualification           calibration,
                                                  recovery/security tests

  WR-09                   24/7 governed operation Queues, schedules,
                                                  budgets, resumability,
                                                  alerts, safe autonomous
                                                  research
  -----------------------------------------------------------------------

## 14.1 First executable vertical slice

29. Import one authoritative manuscript and its supporting research
    files.

30. Resolve manuscript version authority and preserve all originals.

31. Create one BookProject and one ChapterContract.

32. Create three research questions and claim/evidence records.

33. Generate one bounded scene or section in a working branch.

34. Run continuity and factual validators.

35. Create developmental and line-edit findings.

36. Present the author with prose diff, evidence map, unresolved risks,
    and approval controls.

37. Export a reviewed DOCX with provenance and release evidence.

# 15. Archive-Level Disposition Rules

  -----------------------------------------------------------------------
  **Disposition**         **Definition**          **Action**
  ----------------------- ----------------------- -----------------------
  CANONICAL               Accepted governing      Maintain under version
                          source for a capability control; one authority

  SUPPORTING              Valuable                Link to canonical
                          doctrine/examples but   capability
                          not governing           

  HISTORICAL              Superseded but needed   Read-only archive
                          for lineage             

  DUPLICATE               Exact SHA-256 duplicate Collapse active copies
                                                  after acceptance

  DERIVATIVE              Generated/packaged copy Record derivation;
                          with no unique          remove from active
                          authority               authority

  IMPLEMENTATION          Code/schema/tool used   Test, version, and
                          by system               release independently

  REJECTED                Unsafe, unsupported,    Retain rationale;
                          obsolete, or            exclude from execution
                          contradictory           
  -----------------------------------------------------------------------

# 16. File Inventory Summary

  -----------------------------------------------------------------------
  **Extension**                       **Count**
  ----------------------------------- -----------------------------------
  .docx                               322

  .md                                 127

  .txt                                8

  .json                               7

  .zip                                6

  .py                                 4

  .4                                  3

  .4 2                                2

  .html                               2

  .0                                  1

  .3                                  1

  .pdf                                1

  .sha256                             1
  -----------------------------------------------------------------------

## 16.1 Largest analyzed textual assets

  -------------------------------------------------------------------------------------------------------------------------------------------------
  **Path**                                                                                          **Words**               **Size**
  ------------------------------------------------------------------------------------------------- ----------------------- -----------------------
  ULTIMATE_PROSE_SYSTEM_v2.0_COMPLETE.docx                                                          35,303                  135.4 KB

  ULTIMATE_PROSE_SYSTEM_v2.1_IMPLEMENTATION.md                                                      10,373                  72.8 KB

  FORGE_v3_3_Complete.docx                                                                          10,045                  47.0 KB

  AI_Provenance_Verification_System_v2.0.docx                                                       8,382                   60.7 KB

  CFNetworkDownload_ZMXQIY.md                                                                       6,101                   40.9 KB

  Writing_Architect_Complete_Audit_Report.docx                                                      4,973                   28.8 KB

  Unified_Writers_Room_System_v4.1.docx                                                             4,834                   50.3 KB

  THE_ULTIMATE_AI_DETECTION_SYSTEM_v2.0_Implementation_Plan.docx                                    4,667                   52.1 KB

  MODULE_7_AI_Provenance_Verification_Implementation_Guide.docx                                     4,320                   51.6 KB

  The_Voice_Architect.docx                                                                          4,258                   35.6 KB

  SOVEREIGN_v1.2_ReadAloud_Update.docx                                                              4,199                   27.2 KB

  AI_Provenance_Verification_System_v1.0.docx                                                       3,830                   47.9 KB

  NARRATIVEOS_v21_COMPLETE_MASTER_SYSTEM.docx                                                       3,805                   48.1 KB

  UNHINDERED_MASTERY_SYSTEM_v2_0_2026-06-21/UNHINDERED_MASTERY_SYSTEM_v2_0_2026-06-21/SYSTEM/UNHI   3,256                   24.3 KB

  UNHINDERED_Mastery_Operating_System_v2_0.docx                                                     3,115                   50.4 KB

  Module writing/WRITING_SYSTEM/BIBLE_DATA/06\_-\_Scene_Pressure_Matrix.md                          3,021                   19.4 KB

  Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/06\_-\_Scene_Pressure_Matrix.md  3,021                   19.4 KB

  Complete_Author_Profile.docx                                                                      2,988                   134.3 KB

  The_Held-Breath_Voice.docx                                                                        2,713                   18.8 KB

  NARRATIVEOS_v24_1_HARDENED_SYSTEM.docx                                                            2,659                   43.9 KB

  Module writing/WRITING_SYSTEM/BIBLE_DATA/Hebrew_Name_Etymology_Bank.md                            2,509                   15.5 KB

  Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/Hebrew_Name_Etymology_Bank.md    2,509                   15.5 KB

  Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2_EXPANDED/SOV_v1.2.0/04_PRODUCTS/HXIA/RSH   2,174                   15.2 KB

  Module writing/WRITING_SYSTEM/BIBLE_DATA/Iron_Age_Material_Culture.md                             2,148                   13.7 KB

  Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/Iron_Age_Material_Culture.md     2,148                   13.7 KB

  Module writing/WRITING_SYSTEM/BIBLE_DATA/01\_-\_Prose_Style_Reference.md                          2,000                   13.3 KB

  Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/01\_-\_Prose_Style_Reference.md  2,000                   13.3 KB

  Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2_EXPANDED/SOV_v1.2.0/ASSET_REGISTRY.json    1,913                   20.2 KB

  NARRATIVEOS_v24_4_FINAL_SYSTEM.docx                                                               1,902                   42.0 KB

  Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2_EXPANDED/SOV_v1.2.0/Bootstrap_Package/01   1,899                   20.2 KB

  Book_Bible.docx                                                                                   1,871                   27.1 KB

  Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2_EXPANDED/SOV_v1.2.0/01_ENGINEERING/RESEA   1,860                   13.1 KB

  Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2_EXPANDED/SOV_v1.2.0/01_ENGINEERING/ARCH/   1,790                   13.6 KB

  Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2_EXPANDED/SOV_v1.2.0/01_ENGINEERING/SEM-0   1,699                   13.2 KB

  Module writing/WRITING_SYSTEM/ENGINE/NARRATIVEOS_EXECUTION_ENGINE_HARDENED_NEXT3.docx             1,693                   42.9 KB
  -------------------------------------------------------------------------------------------------------------------------------------------------

## 16.2 Exact duplicate evidence

164 exact duplicate groups were identified by SHA-256. The following
sample demonstrates the repeated-package pattern; the full
machine-readable inventory remains available from the forensic run.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Copies**              **Example path A**                                                               **Example path B**
  ----------------------- -------------------------------------------------------------------------------- ------------------------------------------------------------------------------------
  5                       SOVEREIGN_MASTER_v1.4                                                            Module writing/SOVEREIGN_MASTER_v1.4

  2                       Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2.zip                       Module writing_EXPANDED/Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2.zip

  2                       Module writing/WRITING_SYSTEM.zip                                                Module writing_EXPANDED/Module writing/WRITING_SYSTEM.zip

  2                       Module writing/WRITING_SYSTEM/CH01_BLUEPRINT.docx                                Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/CH01_BLUEPRINT.docx

  2                       Module writing/WRITING_SYSTEM/SYSTEM_README.docx                                 Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/SYSTEM_README.docx

  2                       Module writing/WRITING_SYSTEM/CH02_BLUEPRINT.docx                                Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/CH02_BLUEPRINT.docx

  2                       Module writing/WRITING_SYSTEM/MANUSCRIPT_MASTER.docx                             Module writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/MANUSCRIPT_MASTER.docx

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_EDIT_PACKET_SCHEMA.docx     Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_EDIT_PAC

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_POST_MERGE_VALIDATION.docx  Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_POST_MER

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/EVALUATION_GATE_LAYER.docx       Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/EVALUATION_GA

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/ARTIFACT_LINEAGE_LAYER.docx      Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/ARTIFACT_LINE

  2                       Module                                                                           Module
                          writing/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_EXTRACTION_CONTROL_LAYER.docx      writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_EXTRACTI

  2                       Module                                                                           Module
                          writing/WRITING_SYSTEM/RELIABILITY_LAYER/PROMOTION_GATE_HARDENING_LAYER.docx     writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/PROMOTION_GAT

  2                       Module                                                                           Module
                          writing/WRITING_SYSTEM/RELIABILITY_LAYER/RUNTIME_RELIABILITY_INTEGRATION_RULES   writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/RUNTIME_RELIA

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/CHECKPOINT_REGISTRY_LAYER.docx   Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/CHECKPOINT_RE

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/CONTEXT_BUDGET_CONTROLLER.docx   Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/CONTEXT_BUDGE

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/TYPED_RUNTIME_STATE_SCHEMA.docx  Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/TYPED_RUNTIME

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/ARTIFACT_FINGERPRINT_LAYER.docx  Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/ARTIFACT_FING

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_REINTEGRATION_RULES.docx    Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/BAND_REINTEGR

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/REGRESSION_TEST_PACKS.docx       Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/REGRESSION_TE

  2                       Module writing/WRITING_SYSTEM/RELIABILITY_LAYER/DISPLAY_ROUTING_LAYER.docx       Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/RELIABILITY_LAYER/DISPLAY_ROUTI

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/00\_-\_Master_Command_Center_UPDATED.md Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/00\_-\_Master_Command\_

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/Hebrew_Name_Etymology_Bank.md           Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/Hebrew_Name_Etymolog

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/01\_-\_Prose_Style_Reference.md         Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/01\_-\_Prose_Style_Ref

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/06\_-\_Scene_Pressure_Matrix.md         Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/06\_-\_Scene_Pressure\_

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/Kingship_Ideology.md                    Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/Kingship_Ideology.md

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/08\_-\_Templater_Setup_and_Templates.md Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/08\_-\_Templater_Setup

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/09\_-\_Graph_View_Configuration.md      Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/09\_-\_Graph_View_Conf

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/Iron_Age_Material_Culture.md            Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/Iron_Age_Material_Cu

  2                       Module writing/WRITING_SYSTEM/BIBLE_DATA/Index\_-\_Motifs\_-\_Volume_II.md       Module
                                                                                                           writing/WRITING_SYSTEM_EXPANDED/WRITING_SYSTEM/BIBLE_DATA/Index\_-\_Motifs\_-\_Vol
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# 17. Research Sources and Evidence Base

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ID**                  **Source**              **URL**
  ----------------------- ----------------------- ----------------------------------------------------------------------------------------------------------------------------------
  R1                      Wang et al. (2025),     https://aclanthology.org/2025.naacl-long.63/
                          Generating Long-form    
                          Story Using Dynamic     
                          Hierarchical Outlining  
                          with                    
                          Memory-Enhancement,     
                          NAACL.                  

  R2                      Xiong et al. (2025),    https://arxiv.org/abs/2503.08275
                          Beyond Outlining:       
                          Heterogeneous Recursive 
                          Planning for Adaptive   
                          Long-form Writing,      
                          EMNLP/arXiv.            

  R3                      Xia et al. (2025),      https://arxiv.org/abs/2506.16445
                          StoryWriter: A          
                          Multi-Agent Framework   
                          for Long Story          
                          Generation.             

  R4                      Wu et al. (2025/2026),  https://arxiv.org/abs/2506.04180
                          SuperWriter:            
                          Reflection-Driven       
                          Long-Form Generation.   

  R5                      Du et al. (2025),       https://arxiv.org/abs/2512.17179
                          Highlight-Guided        
                          Long-Document           
                          Summarisation with      
                          Self-Planning.          

  R6                      Penguin Random House,   https://authornews.penguinrandomhouse.com/how-prh-turns-your-manuscript-into-a-printed-book/
                          How PRH Turns Your      
                          Manuscript into a       
                          Printed Book.           

  R7                      Penguin Random House,   https://authornews.penguinrandomhouse.com/qa-with-managing-editorial-the-traffic-control-center-of-the-books-production-process/
                          Q&A with Managing       
                          Editorial.              

  R8                      Penguin Random House,   https://authornews.penguinrandomhouse.com/behind-the-scenes-designing-your-books-interior/
                          Behind the Scenes:      
                          Designing Your Book's   
                          Interior.               

  R9                      Taylor & Francis        https://authorservices.taylorandfrancis.com/publish-your-book/writing-editing-services/publishing-guidelines/
                          Publishing Guidelines,  
                          including AI-use        
                          declaration guidance.   

  R10                     EMNLP 2025 Findings, A  https://aclanthology.org/2025.findings-emnlp.750/
                          Survey on LLMs for      
                          Story Generation.       
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

Research limitation: the online literature does not establish a method
that guarantees flawless books. Current findings support planning,
decomposition, retrieval, reflection, and multi-stage evaluation, but
human judgment, source verification, and controlled release remain
necessary.

# 18. Final Acceptance Decision

  -----------------------------------------------------------------------
  **ARCHIVE ACCEPTANCE: ACCEPT AS SOURCE CORPUS; REJECT AS PRODUCTION
  SYSTEM\
  **WRITING_ARCHITECT contains strong intellectual assets and should be
  preserved as the authoritative seed corpus for the writing domain. It
  is not acceptable for direct 24/7 autonomous operation because
  authority is ambiguous, duplication is extensive, enforcement is
  prompt-based, research evidence is not normalized, scoring is not
  calibrated, and release claims are not backed by executable gates.
  -----------------------------------------------------------------------

  -----------------------------------------------------------------------

## Mandatory next action

Begin WR-00 --- Forensic Baseline and Authority Resolution. Produce the
immutable archive hash, full recursive manifest, duplicate and
derivative classification, capability map, version/supersession graph,
and proposed canonical authority for each writing capability. Do not
create another integrated "master" document until WR-00 is accepted.

End of report.
