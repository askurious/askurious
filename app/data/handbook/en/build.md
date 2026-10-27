# Building a survey

This page walks through the pages on which a survey is made: from the list of surveys through settings, research plan, questionnaire, flow, quotas and translation to the preview.
For each page it says what the page is for, what the main settings mean, which trade-off a setting decides, and why the page exists.
The reasons come from the recorded design decisions (cited by date), the vocabulary of the interface and its "Why?" learning aids.

## 1. My surveys

![My surveys: the list of surveys with the three create buttons](img/dashboard.jpg)

**What it is.**
The start page after signing in.
It lists all surveys a person may open (their own and those shared with them or their groups), with status, number of completed real participations and dummy participations.
Three buttons create the three kinds of survey: **+ Questionnaire**, **+ Interview guide**, **+ Live poll**.

**Main controls.**
Search, filter by kind and group, sort (last changed, title, last created, most participations) across all your surveys, pages of 10, 20 or 50 surveys, pin to the top, and quick links per survey (questionnaire, settings, results, export, duplicate, archive).

**Why it looks like this.**
The three kinds have fixed names everywhere (glossary: "Fragebogen, Interviewleitfaden, Live-Abstimmung"), so that a button, a label and a filter always mean the same thing.
The count separates real participations from dummy data, so that nobody mistakes simulated data for fieldwork.

**Signing in.**
Researchers sign in with their ORCID iD (recommended) or with an email address and password.
ORCID links a person to their publications; the order of the researchers on a survey is the author order of the later data package (decisions of 2026-09-26 and 2026-10-01).

---

## 2. Creating a survey

![The wizard for a new questionnaire: start empty, from a template or from a file](img/new-survey.jpg)

**What it is.**
A wizard with three ways to start:

- **Start empty**: six steps, each answers one question, then an overview. **What is it about?** Title, description, research question, research goals, project, keywords and a topic from the CESSDA topic list, languages. **Who takes part?** Research design and recruitment as cards (recruitment split into “with chance” and “without chance”, the term of the DDI vocabulary under the chosen card), population, country (preset for German), region, sample target and which details about the person are asked (none, age and sex, or the short RatSWD set; swappable while the block is unchanged); hypotheses, exclusions and exclusion rules in a fold. **Responsibility:** people involved with their role, responsible persons with their organisation, contact email and funding (funder and grant number apart). **Participation:** set as recommended, adjustable in a fold. **Consent:** minimum age, special categories, ethics approval, compensation, open data with licence; controller and data protection officer as this installation, changeable in a fold. **Appearance:** colours and font; you choose the style of AI images with the first AI image. The overview (“All details at a glance”) shows in the box “For data archives” which details an archive gets, which Askurious sets itself and which are missing. From there you go on to the questionnaire or, if AI is on for you, with “Have AI suggest matching scales” to the suggestions in the questionnaire. Diary surveys and interview guides have the same assistant with steps of their own (sections 17 and 18). You set the short name of the link under “Data collection” with the participation link.
- **From a template**: one of six typical study designs, each with a research plan, a questionnaire and 120 dummy participations, so that the results page can be read at once.
- **From a file**: an exported survey file (.json or .zip), e.g. from a colleague.

![The six study templates](img/new-from-template.jpg)

| Template | Research goal | What it shows |
|---|---|---|
| Customer satisfaction with recommendation | describe | recommendation (0 to 10), a Likert scale, a group question, an open question |
| Technology acceptance (TAM) | test relationships | validated scales from the pool: usefulness, ease of use, intention to use, technology readiness |
| Online experiment with two conditions | test an effect | randomisation in the flow: one of two texts per person, then the same attitude scale |
| Conjoint for a product | measure preferences | three attributes, an experimental design, part-worths and a market simulator |
| Vignette study with 2 × 3 factors | test an effect | a loop with factors and levels: several short descriptions per person |
| Panel survey with quotas | describe | panel identifier from the link, completion links, quotas by age, gender and federal state |

The same three ways are at the top of the pages for a new live poll and a new interview guide. There the templates are ready-made polls (feedback on the session, muddiest point, prior knowledge, warm-up and mood) and the guide forms problem-centred interview, expert interview and narrative interview.

**Why.**
The templates are for students and newcomers: a complete study is easier to learn from than an empty page.
Their dummy answers follow the research plan for the variables of the hypotheses and are random for everything else; the page says so, because the results then show what was assumed, not what would come out.

The **description** of a survey is asked for at once, because it does two jobs: it appears in the consent ("What is it about?") and it gives the AI support its context.
The word counter asks for at least about 15 words.

Every new questionnaire starts with a consent block and a block "Demographics" with age and gender from the construct pool, so that these questions count towards the pooled analysis of constructs.

---

## 3. The tabs of a questionnaire

![The head of a survey: title, status, preview, save state, undo and the row of tabs](img/editor.jpg)

All pages of one questionnaire share one header:

- the title and the **status** (Draft, Running, Paused, Closed);
- **Preview ↗**, which opens the survey as participants see it, in a new window;
- the **save state** ("Changes are saved automatically.") and undo / redo;
- a row of **tabs**, in three groups separated by thin lines; one more line sets the settings apart:

| Group | Tabs |
|---|---|
| Plan | Settings · Research plan · Questionnaire · Flow (with the quotas) · Schedule (diary surveys only) · Translation (with several languages) |
| Collect | Data collection · Quality assurance |
| Analysis | Results · Dashboards · Export |

In the menu "Help and tools" of the header, "Help on this page" opens the section of this handbook on the tab that is open.

**Why.**
The groups follow the order of the work (UI/UX review of 2026-10-03).
There is no "Save" button: every change saves itself, and the one save line in the header reports it for every page (decision of 2026-10-07).
Undo works across pages and checks the role when it is carried out.

---

## 4. Settings

![Settings: foldable groups, each with a one-line summary](img/settings.jpg)

**What it is.**
Everything about the survey that is not a question, in foldable groups: **Survey** (title, project, description, languages), **Researchers involved**, **Participation**, **Consent and data protection**, **End pages**, **Appearance** (with the **standard of the scales**: points, answer scale, labels and numbers that every new Likert scale takes; “apply to all scales” aligns the existing ones, the pre-launch check reports deviations, deviations on purpose are ticked in the element).
The wizard and this page use the same fields, so a setting means the same in both places.
Once real participations exist, the settings that the consent names are locked (shown with a padlock).

### Participation

| Setting | Meaning | Trade-off |
|---|---|---|
| Allow back button | participants can return to the previous page | Going back lets people correct mistakes, but also lets them adjust earlier answers to later questions; randomised orders and choice tasks stay fixed either way. |
| Show progress bar | a bar at the top of each page | A steady bar hardly reduces break-offs; one that moves slowly at the start increases them (Villar et al., 2013). With long questionnaires and many branches, leave it out. |
| Advance automatically | pages answered with one click move on automatically | Faster for the participant, but a misclick is not seen; keyboard users are never moved on automatically. |
| Participation in the same browser | once per session; once and then blocked for some days (a cookie without an identifier); any number of times (lab computers) | Blocking keeps out repeat participations in the same browser, but not from another browser or a private window, and the cookie must be named in the consent. For panels, the per-person links ("each value only once") are the stronger tool. |
| Field end, maximum participations | the survey closes automatically on a date or at a number | Protects a budget or a panel quota; a field end means 23:59 Berlin time of that day. |

### Paradata and data quality

| Setting | Meaning | Trade-off |
|---|---|---|
| Record technical details | device type, operating system, browser without version, window width in classes | Explains differences between devices, but every additional detail moves towards a browser fingerprint; therefore coarse classes only, and named in the consent. |
| Record time per page | seconds from showing a page to "Next" | Needed for the Relative Speed Index and the speed prompt; adds one column per page to the export. |
| Count window switches and pasting | how often someone leaves the tab or pastes text, only numbers, never content | Helps to spot copied answers and distraction; it is a mild form of observation, so it is opt-in and named in the consent. |
| Bait for AI agents | a sentence that people neither see nor hear asks AI agents for a password | Finds some automated participations at no cost to people; finds only some agents, and is named in the consent. |
| Speed prompt | a page submitted faster than it can be read is shown again once with a request to check the answers | A single prompt improves data as well as repeated prompts without raising break-offs (Conrad et al., 2017). The fixed reading time (350 ms per word) is the same for everyone; the median rule adapts to the page but always hits the fastest, even if everyone is careful. Blocks with sensitive questions can be left out, because prompts can strengthen socially desirable answers (Zhang & Conrad, 2018). |

### Consent and data protection

| Setting | Meaning | Trade-off |
|---|---|---|
| The survey collects special categories (Art. 9 GDPR) | the standard text names Art. 9(2)(a), and participants give explicit consent with a tick of its own; fixed from the start | Health, origin, beliefs, sex life and the like need explicit consent. The pre-launch check reminds you to clarify a data protection impact assessment with your data protection officer. If the survey contains a voice message, a tick of its own for the audio recording is added in the same way. |
| Funding | funder and grant number, shown in the consent with the topic of the survey; empty: no sentence | Many funders ask to be named; the field is free text, since grant numbers follow no fixed pattern. |
| Controller under the GDPR, data protection officer | shown on the privacy page of the survey; empty: the details of this installation | The privacy page adds the participants' rights and the supervisory authority, the standard consent text the legal basis, withdrawal and recipients (Art. 13 GDPR). Researchers of another institution enter their own. |
| Deletion code | the end page and the privacy page show each person a code with which they can later delete their own answers, also from another device; off until switched on | A strong right for participants; the code is only useful if people see it, so end links that redirect at once hide it (the pre-launch check warns). Only a hash of the code is stored. |
| Estimate country and federal state from the IP address | looked up once at consent, in a table on the server; the IP address itself is never stored | Gives a region without a question, but the federal state is an estimate (mobile, company and university networks, VPN often show another one). Use the element type "Postcode" for anything finer. |
| Delete identifiers and contact details after … | clears panel IDs and the separated section after a period (new surveys: 6 months) | The answers stay with their participation ID, so pseudonymous (a participation ID passed on to a panel or web app still links them there); six months suffice for payments and prize draws. |
| Delete all participations after … | deletes all answers after a period (new surveys: 10 years) | Ten years is the usual retention of research data (DFG code of conduct); the platform is not an archive, so data should be exported or published before. |
| Seal chain | every finished real participation gets a case number and an entry in a hash chain; deletions leave a deletion record with a reason; the newest hash is time-stamped by the DFN within about 10 minutes; every hour Askurious checks the stored data against the chain and reports a break | Makes later changes or silent removals visible, also to reviewers and against the platform itself. Once the chain has an entry it cannot be switched off, because a chain that stops would hide what is deleted afterwards. On for new surveys. [Example of a public check](https://askurious.de/verify/42-6-f5997ff3715b31a9) |
| Do not list in the admins' overview | hides title and counts from the platform admins' list | Admins never see questions or answers anyway; the tick also hides title and status. |

**Why these defaults.**
Participants are anonymous by default: no IP address, no fingerprinting (decision of 2026-09-26).
Every data-collecting option that goes beyond answers is opt-in and is named automatically in the consent, so that the consent always matches what is collected.
Retention periods and deletion codes (decisions of 2026-10-03) turn data-protection duties into something the tool does automatically.

### Appearance

![Appearance: designs of the platform, own colours, a live sample page in desktop and phone width](img/appearance.jpg)

The **Appearance** page sets the colours, font and logo of the participant pages, with a live sample page.
Only a few base colours can be chosen; all other tones are derived and checked for a contrast of at least 4.5:1, and there is no free CSS and no web font (decision of 2026-10-01).
The trade-off is less freedom in exchange for pages that stay readable, accessible and free of requests to third parties.
Designs can be saved under a name and shared with a group. If one's organisation has an **organisation design**, it is at the top of the choices; the survey gets a copy that can be changed freely, while only the organisation's admins change the organisation design itself.

---

## 5. Research plan

![Research plan: research question, goals, design and hypotheses with their tests](img/research-plan.jpg)

**What it is.**
The plan of the study, before the questionnaire: research question (one sentence), research goals (describe, test relationships, compare groups, test an effect, measure preferences, …), design (e.g. cross-sectional survey, experiment), hypotheses and the sample target.
A hypothesis names two variables of the questionnaire, the direction, and the method; "auto" picks the test from the level of measurement.
The type **Descriptive assumption** (a value, share or ranking, such as "level X has the highest part-worth in the conjoint" or "more than half agree") names only the measured variable; the results report its value without a significance test.
In the element, the section **In the research plan** shows the roles to tick for each hypothesis, each with a line on what it means for this type (e.g. "Groups: splits the respondents into groups").
In the **Model** view (path diagram) the variables on the left are the questions of the questionnaire: a new variable comes with a new question in the questionnaire, and a scale with several statements (such as a construct) appears as an ellipse. **Model as text** says the same in words, including variables without a relation and hypotheses not yet in the picture.

**Why.**
The plan is the backbone of everything that follows (decision of 2026-10-06):

- the **power analysis** turns the smallest effect of interest into a sample target ("84 usable · 94 completions · at 10 % exclusions");
- the **results** test each hypothesis as planned and say whether it is supported, with the sentence for the report;
- the **pre-registration** is frozen and time-stamped at the start of data collection; changes after the start are logged as deviations;
- the **methods section** describes design, hypotheses and tests.

In a longitudinal study the section **Waves** below the plan links the surveys of the waves by the personal code and sets which variables the results page models over the waves. The hypothesis type **Effect over time** (X in one wave on Y in the next) is tested in the cross-lagged panel model; its power can be checked by simulation.

A plan written before the data protects against HARKing (hypothesising after the results are known); tests that are not in the plan are reported as exploratory.

---

## 6. Questionnaire editor

![The questionnaire editor: element types on the left, blocks in the middle, pre-launch check on the right](img/editor.jpg)

**What it is.**
Three columns:

The left column distinguishes your access role from the state of the survey. With “View” you cannot edit; a running or paused survey explains separately why changes are currently restricted.
Someone who may only look but may make a copy (for example of an example survey, with the right to create surveys) finds “Take a copy of your own” there.

- **Add** (left): the element types (38, from single choice and Likert matrix to conjoint, MaxDiff, maps, image marking and live feedback), the validated constructs from the pool, and "Draft a scale with AI".
  Drag into a block or click **+**.
- **Blocks** (middle): the consent first (block 0), then the blocks with their elements; page breaks divide a block into pages.
  **+ New block** is under the last block. ▾ before the name folds a block (it then only shows how many elements and pages it has); the browser remembers this. ▷ opens the preview of the block.
  Constructs show a green badge "validated scale".
- **Pre-launch check** (right): the research plan in one line, and warnings about the questionnaire: too many demanding questions on one page, a missing contact email in the consent, unreplaced placeholders such as "[System]", missing translations, an estimated completion time.

![The element page of a validated construct, with answer mode and level of measurement](img/element-likert.jpg)

Clicking an element opens its own page with the question text, the options, the answer mode, the level of measurement and a live preview.
A validated construct shows its source and terms of use and is locked in its validated wording; "Unlock" is possible, but the element is then no longer the validated instrument and is documented as "adapted".
The copy icon on a question **duplicates** it: the copy stands right below, with texts, images and settings and a variable name of its own (“age” becomes “age_2”). The same icon in the head of a block duplicates the whole block behind it; conditions and placeholders such as {{age}} that refer to a question of the same block point at its copy in the copied block, references outside stay. “Undo” takes either back. If the same questions are to be asked per scenario or product, “Loop around this block” in the settings of the block is often better: one block, repeated per table row, the answers directly comparable. A question goes into another survey from its page, “Use in another survey”.
Deleted questions stay in the bin below the blocks for seven days. Once a survey has been opened for editing, deleting a question with real answers asks first: its answers are then missing from the data set, the results and the exports but stay stored, and the question stays in the bin until it is deleted there for good or restored.

**Settings of an element and their trade-offs.**

| Setting | Trade-off |
|---|---|
| Answer mode: optional, request, required | Required questions prevent gaps but invite random answers or breaking off; “Request” marks the question once and then lets people go on, usually the best middle way. Required mainly for questions without which the rest cannot be analysed (Döring, 2023). |
| Number of scale points | Five to seven labelled points measure most reliably; more add hardly any precision, fewer lose information (Krosnick & Presser, 2010; Revilla et al., 2014). |
| "Don't know" counted as missing | Prevents guessed answers, but is also chosen out of convenience; offer it only where not knowing really occurs. |
| Random order of options or statements | Spreads order effects evenly; not for scales with a natural order or for "Other" at the end. |
| Matrix (grid) or single questions | A grid saves space but invites break-offs and identical answers in every row; on phones single questions give better data (Liu & Cernat, 2018; Mavletova et al., 2018). |
| Slider or labelled points | Sliders take longer and lead to more missing answers (Funke et al., 2011); labelled points are the safer choice. Sliders have no starting position, so "not answered" stays distinguishable from "middle". |
| Drop-down list or option buttons | Option buttons show all answers; in a drop-down people pick the first visible entries more often. A list suits long, familiar lists (federal states, years). |
| Level of measurement (ordinal, metric) | Decides which statistics and tests the results use for this element. |

**Why the editor works this way.**
Element types are plug-ins with one shared interface (decision of 2026-09-26), which is how 38 types fit one editor.
Blocks, not pages, are the unit of flow, randomisation and loops; pages inside a block come from page breaks.
Constructs are ordinary elements that are locked, not a separate type, so they work everywhere an element works and still count for the pooled reliability.
A survey uses a frozen copy of a construct, so later changes to the pool never change a running study.

---

## 7. Flow

![The flow of an online experiment: consent, a randomisation between two texts, then the same scale](img/flow-experiment.jpg)

**What it is.**
The order of blocks for participants, edited on its own page.
Building blocks on the left:

| Building block | What it does |
|---|---|
| Randomisation | shows one (or more) of several blocks, balanced or at random; the assignment is stored and exported |
| Condition | shows blocks only for certain answers or URL parameters ("then" / "else") |
| Loop | repeats blocks per row of a table or per vignette; placeholders `{{variable}}` fill texts and images |
| End | a screen-out or "quota full", mostly inside a condition |
| External section | sends participants to a web app (e.g. a reaction-time task) and back, with signed return values |
| Quota calculation | places the person in a quota |
| Web service | the server calls an interface and stores fields of the answer (experimental); [try the demo](https://ask.askurious.de/s/demo-webservices) |

The right column shows key figures (estimated duration, number of blocks, experimental conditions, branches, paths through the flow) and a pre-launch check of the flow.

![The flow of a vignette study: a loop with two factors around one block](img/flow-vignettes.jpg)

**Trade-offs.**

- **Random assignment**: only random assignment makes sure that groups differ in the condition alone, so that a difference can be read as an effect; groups of equal size give the most power. "Balanced" fills the least-shown condition first, which keeps the groups equal even in small samples.
- **Between, within, mixed**: one condition per person avoids carry-over effects but needs more people; all conditions per person needs fewer people but risks order and learning effects; the loop's settings decide (k of N rows).
- **Length**: the longer the announced duration, the fewer people start, and answers get shorter towards the end (Galesic & Bosnjak, 2009); the estimated duration is always in view.

**Why.**
The flow got its own page because nested randomisations and branches did not fit a side bar (decision of 2026-09-26).
Loops belong in the flow, not in a block's settings.
The order is decided once per participation on the server and stored, so reloading and "Back" keep it, and the export can report it.

---

## 8. Quotas

![Quotas of the panel survey: sub-quotas by gender with targets, type and hold time](img/quotas.jpg)

**What it is.**
Targets per group (sub-quotas), e.g. by gender, age and federal state, with a code for the export.
A quota assistant fills targets from the population of Germany (census 2022).
Warnings name answers that fit no sub-quota (e.g. "diverse" or "no answer").
How quotas, completion links and the quota state work together with a panel provider is under [Panel study with quotas](guides.md#panel-study-with-quotas).

| Setting | Trade-off |
|---|---|
| Hard or soft | A hard quota never lets more people into a sub-quota than planned and keeps places free for people already under way; a soft one lets everyone finish who started and exceeds the target by a tolerance. Hard when a panel pays for each participation; soft when nobody should be turned away. |
| Hold time | how long a place stays reserved for someone who has not submitted a page (15 minutes for a new quota); short frees places quickly, long is fairer to slow participants. Who has not submitted any page since the quota calculation gives the place back after 5 minutes already: abandoned participations (scripts too) do not hold places |
| If several sub-quotas fit | first in order, or the one least filled |
| Weighting | makes up for groups that are too rare, but only for the variables used, and large weights reduce the effective sample size |

---

## 9. Translation

![Translation from German to English, with the state of each text and the AI button](img/translation.jpg)

**What it is.**
Every text of the survey in the source language next to its translation, with a state per text: missing, to check, AI unchecked, translated, validated.
Structure (codes, options, scale points) is edited only in the source language; other languages are pure translation layers with a fall-back to the source language.
"Translate everything open with AI" fills the gaps; AI translations stay "AI, unchecked" until a person confirms or edits them.
Validated instruments use their own published version in the target language where there is one.

**Why.**
One logic, many language layers (decision of 2026-09-26): a change to the questionnaire cannot make two language versions drift apart, and the data of all languages land in the same columns.
German has two versions, "Sie" and "du", which participants get through the link.

---

## 10. Preview and pretest

![The preview of a Likert matrix, with the comment panel for pretesters](img/preview-likert.jpg)

**What it is.**
The preview runs the survey exactly as participants will see it, with a bar on top (start over, jump between pages, desktop or mobile width, check required answers on or off) and a comment panel on the right.
Comments can refer to the whole page or one question and can carry a screenshot.
The “Comment” button also tells screen readers how many comments the page already has.
A **pretest link** lets colleagues or pretest participants do the same without an account; their runs count as test runs.

![A choice task (conjoint) in the preview](img/preview-conjoint.jpg)

**Why.**
The preview uses the same page engine as the real survey, so every check behaves the same (decision of 2026-09-26).
The mobile width is a real frame at device width, so the real layout breakpoints apply.
Test runs are stored apart from real data (source "test") and can be kept to try out the export, or replaced by each new run.
