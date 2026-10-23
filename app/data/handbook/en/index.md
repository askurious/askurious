# Askurious: the concept

> **Askurious is a tool for scientific surveys: design a questionnaire, take it into the field, analyse the results and share the data openly, all in one place.**
>
> The name comes from **ask** and **curious** (/ɑːˈskjʊə.ri.əs/).

This handbook describes Askurious for the people who use it: researchers in the social sciences, and students who are learning empirical research.
It explains what the tool is for, which ideas shape it, which words it uses, what each page does and why it is built that way.

| Page | What it covers |
|---|---|
| [The concept](index.md) (this page) | The idea, the principles, the three kinds of survey, the life of a survey, the vocabulary |
| [Guides](guides.md) | Typical studies step by step: the first survey, a panel study with quotas, an online experiment, a seminar with own links per person, a longitudinal study |
| [Building a survey](build.md) | The pages on which a survey is made: what the settings mean, the trade-offs behind them and why they exist |
| [Fieldwork](field.md) | Data collection, quality assurance and what participants see |
| [Analysis](analysis.md) | Results, dashboards, export and open data |
| [Other kinds of survey](kinds.md) | Diary survey, interview guide and live poll |
| [Other pages](more.md) | Constructs, groups, designs, sample size, account, element types and learning aids |
| [AI in Askurious](ai.md) | Where and how AI is used, what is sent, how AI output is labelled, and what AI deliberately does not do |

The screenshots show a demo survey, the study templates and made-up people with dummy data (October 2026), taken with tools/handbook_shots.
The interface is shown in the language of this handbook; the questionnaires from the templates are in German, the language of the templates.
Every page of the interface exists in German and English (Account › Display and language).

## Who it is for

- **Researchers** who run questionnaire studies, experiments, choice experiments (conjoint, MaxDiff), interviews or live polls in lectures.
- **Students and newcomers** writing a thesis or a term paper: learning aids explain each methodological decision where it is made, and study templates show complete, working designs with a research plan and data.
- **Teams**: several researchers work on one survey with equal rights, and groups share surveys and designs with a whole chair or project.

## Principles

These principles shape the whole tool.
Askurious offers sensible defaults and explains them, but it enforces none of them: the researchers decide.
The page descriptions from [Building a survey](build.md) on show where each principle appears.

1. **Method first.**
   A survey starts from a research question and hypotheses (the research plan), not from a list of questions.
   The plan decides the sample target (power analysis), which test the results use, and what goes into the methods section.
2. **Sensible defaults, explained.**
   Every setting has a default that follows the survey literature, and a "Why?" fold that explains the trade-off with a source (Krosnick & Presser, 2010; Döring, 2023, and others).
   Nothing is enforced: researchers may decide otherwise, and the page tells them what they give up.
3. **Nothing is removed silently.**
   Speeders, straightliners and suspected bots are *marked*, not deleted.
   Researchers decide what counts, and the methods section reports it.
4. **Privacy by design.**
   Participants are anonymous by default: no IP address, no browser fingerprint.
   Consent is a structured element that always states what the tool guarantees.
   Contact details for a prize draw are stored so that they cannot be linked to the answers.
   Participants can delete their own answers with a deletion code.
   Retention periods delete data without anyone having to remember it.
5. **Open data with ease.**
   The way from the field to a citable data package is short: codebook, SPSS, jamovi, R, Zenodo, a pre-registration frozen at the start of data collection, and a tamper-evident seal chain.
6. **Reuse instead of reinvention.**
   Validated scales come from a shared pool of constructs.
   They go into the questionnaire as ordinary elements, locked in their validated wording, and every survey that uses them adds to their pooled reliability.
   Your own questions can be reused across surveys as well, as linked elements: their answers can then be analysed together, which makes comparisons and meta-analyses across studies simple.
7. **AI where it suits: suggestions, never decisions.**
   AI drafts, reviews and translates when a researcher asks for it.
   The researcher checks and accepts what fits.
   AI never sees participants' answers, and AI-made content stays labelled ("AI-generated, not validated").
   See [AI in Askurious](ai.md).
8. **Participants come first.**
   The participant pages are accessible (keyboard, screen readers, colour never the only signal) and light on mobile data.
   Updates restart the two participant servers one after the other, so that participants normally see no error page.

## Three kinds of survey

One tool, three ways to ask, with the same editor style, appearance, data protection and export.

| Kind | For | What is special |
|---|---|---|
| **Questionnaire** (Fragebogen) | standardised surveys, online experiments, choice experiments, panel studies | research plan, flow with randomisation and loops, quotas, quality assurance, results with tests, dashboards | {kind:survey}
| **Interview guide** (Interviewleitfaden) | guided, qualitative interviews | topic blocks and guide questions after Helfferich (2011), an interview view for the tablet, audio recording, local transcription, export to MAXQDA, ATLAS.ti and NVivo | {kind:interview}
| **Live poll** (Live-Abstimmung) | feedback in lectures and at events | presenter view and audience view, word clouds, answers on the phone without sign-in | {kind:live}

## The life of a questionnaire

```
  idea ─► research plan ─► questionnaire ─► flow ─► preview & pretest ─► start
                                                                           │
  open data ◄─ export ◄─ results ◄─ quality assurance ◄─ data collection ◄───┘
```

- **Draft**: everything can be changed.
  Test runs and dummy data let researchers check the questionnaire and the analysis before a single real person takes part.
- **Running**: participants can take part.
  Text corrections are allowed; changes to the logic need a new revision of the questionnaire, so that the data stay interpretable.
- **Paused / closed**: no new participants; people who started may finish.
  A closed survey can be reopened, but never goes back to draft.
- **Archived**: tidied away from the list, for drafts and closed surveys.

Every participation has a **source**: real, test run or dummy.
Only real participations count towards quotas, balancing and automatic closing; the export always says which source a row comes from.

## Vocabulary

Askurious uses a fixed set of words everywhere in the interface.
The terms below are the ones a social scientist or a student meets first.

### Building a questionnaire

| Term | German | Meaning |
|---|---|---|
| survey | Befragung | the whole that researchers create, take into the field and analyse |
| element, element type | Element, Elementtyp | one unit of the questionnaire: a question, a text, an image, a page break, a computed value; there are 38 element types |
| statement | Aussage | one row of a Likert matrix, a slider or MaxDiff (often called "item" elsewhere) |
| option | Option | one answer possibility of an element |
| block | Block | a group of elements; the unit of the flow, of randomisation and of loops; consent is block 0 |
| page | Seite | made inside a block by page breaks |
| construct | Konstrukt | a validated scale or standard question from the shared pool, e.g. the Big Five Inventory (BFI-10) |
| subscale | Subskala | one dimension of a multidimensional scale, with its own scale score |
| answer mode | Antwortmodus | optional, request or required, per question |

### Designing the study

| Term | German | Meaning |
|---|---|---|
| research plan | Studienplan | research question, goals, design, hypotheses, sample target and analysis plan |
| hypothesis | Hypothese | a relationship, difference or mediation between two variables of the questionnaire, with the planned test |
| flow | Ablauf | the order of blocks for participants, with randomisation, conditions and loops |
| randomisation | Randomisierung | shows one (or more) of several blocks at random; balanced or purely random |
| experimental conditions | Versuchsbedingungen | the cells of a between-subjects design that come from randomisations |
| condition | Bedingung | shows blocks only for certain answers ("then" / "else") |
| loop | Schleife | repeats blocks for each row of a table or each vignette; its columns are placeholders |
| vignette, factor, level | Vignette, Faktor, Stufe | short descriptions built from factors and their levels (factorial survey) |
| attribute, level | Merkmal, Stufe | the properties of a choice task (conjoint) and their values, e.g. price with €9, €12, €15 |
| quota, sub-quota | Quote, Teilquote | targets per group, e.g. by age and gender; hard or soft |
| external section | externer Abschnitt | sends participants to another web app (e.g. a reaction-time experiment) and back, with signed return values |
| separated section | getrennter Abschnitt | the part after it (e.g. an email for a prize draw) is stored so that it cannot be linked to the answers |

### Collecting data

| Term | German | Meaning |
|---|---|---|
| participation link | Teilnahmelink | the link through which participants open the survey; can have a short name (`/s/mensa-studie`) |
| pretest link | Pretest-Link | a link to test without an account; answers and comments count as test runs |
| test run | Testdurchlauf | a run through the preview, stored as `source = test` |
| dummy data | Dummy-Daten | simulated participations through the real flow, to try out the analysis before fieldwork |
| sample target | Stichprobenziel | completions the survey should reach: usable participations plus expected exclusions |
| usable participations | verwertbare Teilnahmen | completions that pass the quality rules and go into the analysis |
| exclusions | Ausschlüsse | completions that the quality rules take out; break-offs are not completions |
| end page | Endseite | what comes after the last page, e.g. with the deletion code |
| deletion code | Löschcode | a code a participant can use to delete their own answers later, also from another device |

### Analysing

| Term | German | Meaning |
|---|---|---|
| basis of the analysis | Grundlage der Auswertung | data pool, how marked participations are treated, and weighting; at the top of quality assurance and results |
| data pool | Datenpool | which sources (real, test, dummy) and which states (completed, broken off, screen-out) count |
| speeder | Speeder | someone faster than a share of the median time (Leiner, 2019) |
| Relative Speed Index (RSI) | Relative Speed Index | speed relative to the median of each page; above 2 means twice as fast as typical |
| α, ω | Cronbachs Alpha, McDonalds Omega | reliability of a scale; ω does not assume that all statements contribute equally |
| part-worth, importance | Teilnutzen, Wichtigkeit | results of a choice model: how much a level favours the choice, and how much an attribute moves it |
| segment | Segment | a group with similar preferences from a latent class analysis |
| methods section | Methodenteil | a draft of the methods section of a manuscript, from the survey's own figures |
| seal chain | Siegelkette | a hash chain over all participations, time-stamped by the DFN, that shows whether data were changed or removed afterwards |

### People and roles

| Term | German | Meaning |
|---|---|---|
| researchers, participants | Forschende, Teilnehmende | the two kinds of people; never "respondents" |
| roles | Rollen | Admin, Edit, View, per survey |
| group | Gruppe | a working group (chair, project) that shares surveys and designs; it owns nothing |
| institution | Einrichtung | where a researcher works, taken from ORCID |
| learning aids | Lernhilfen | the "Why?" folds and the glossary of statistics, for newcomers; switchable on Account › Display and language |
