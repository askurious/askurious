# Fieldwork

This page describes what happens during data collection: participation links and invitations, quality assurance and what participants see.
How a whole study with a panel provider runs is under [Guides](guides.md#panel-study-with-quotas).

## 11. Data collection

![Data collection: the participation link with QR code, short name, links per group and panel connection](img/field-links.jpg)

**What it is.**
Everything about being in the field:

- **Testing**: preview, pretest links, test runs and **dummy data** (simulated people that go through the real flow, to test analysis scripts before fieldwork).
- **Status of the survey**: the key figures (since when, until when, completed against the target, per day in the field, completion time), the **timeline** of participations per day, who is in the survey right now, and the buttons to start, pause, close and archive.
- **Participation link**: one link for everyone, a short name (`/s/mensa-studie`), a QR code, own links per person or group (e.g. per course, to see how many take part through each), and the **panel connection** (identifiers from the link, completion links for complete, screen-out and quota full).
- **Invitations by email** (when the platform sends email): an address list, a personal link for each person, reminders. The list does not limit who takes part while “Take part only with a personal link” is off: the general link keeps working, and a personal link passed on sets the state of the invited person. With the tick the survey takes only people with a personal link, each link for one participation.
- **Quotas** in short.

Whoever started before fieldwork ended (end of fieldwork, closing, a pause) can still finish for 24 hours; after that the survey shows “ended”, and the participation stays “started”. An archived survey takes nothing any more.

![Testing: pretest links and dummy data](img/field.jpg)

**Why.**
"Data collection" is the name of the tab; "in the field" is the state of a running survey (glossary).
Every participation carries its source (real, test, dummy), and only real ones count for quotas, balancing and automatic closing (decision of 2026-09-26), so testing can never spoil the data.

### Panel connection {kind:survey}

IDs from the link, completion links, rules per provider, signed links and the quota state for the provider: the whole study with a panel provider is described in the guide [Panel study with quotas](guides.md#panel-study-with-quotas).

---

## 12. Quality assurance

![Quality assurance: basis of the analysis and completion times with the speeder threshold](img/quality.jpg)

**What it is.**
Checks of data quality, each with the participations it marks:

- **Basis of the analysis**: which sources and states count (data pool), whether marked participations are left out, and weighting.
  The same basis applies to results, dashboards, exports and the methods section.
- **Completion times and speeders**: a histogram of completion times with an adjustable threshold, by total duration (faster than a share of the median, default 50 %; Leiner, 2019) or by Relative Speed Index (per page).
- **Response patterns**: straightlining, long strings of identical answers, low variation within a person.
- **Attention checks**, **bots and AI agents**, **speed per page**.
- A paragraph **for the methods section**.

The page computes its figures once and keeps them until participations, data pool, threshold, marking rule or questionnaire change; every such change applies at the next visit. From 5000 participations on, the first computation runs in a process of its own: until it is done a waiting page says “The report is being prepared”, then the page opens by itself.

| Choice | Trade-off |
|---|---|
| Total duration or Relative Speed Index | The total duration is easy to explain but penalises questionnaires with paths of different length; the RSI compares each page with its own median and is more precise, but needs page times. |
| Which participations count | Results apply to real, completed participations. Including broken-off ones raises the number of cases but biases results if certain people break off more often. |
| Leave out marked participations or only mark them | Leaving out protects the results from careless answers; keeping them avoids excluding careful but fast people. The page shows both, and the exclusions are fixed best before data collection. |

**Why.**
Nothing is stored and nothing is removed by this page: it marks, the researcher decides, and the methods section reports the rule and the number.
Some checks only report from a minimum number of cases, so that one odd person does not look like a pattern.

---

## 16. What participants see

![The consent page in the preview](img/preview-consent.jpg)

1. **Consent** (block 0): a structured text that always states voluntariness, anonymity and the right to stop, plus what the survey collects; explicit check boxes are needed to agree, not to decline.
   Anyone who declines leaves no data: nothing is stored except a counter without time.
   Answers given before the consent (e.g. a screening) are discarded on a decline.
2. **Pages**: the order is fixed on the server once per person; "Next" checks required answers (red) and reminders (amber), always with a text, never colour alone.
   A session lasts seven days, so people can pause and come back.
3. **Live feedback** (optional): participants can see where they stand compared with everyone so far, e.g. as a figure in a crowd or on a bell curve; this raises satisfaction with the survey (Kühne & Kroh, 2018).
   If the feedback fixes the answers, they stay visible on going back but can no longer be changed; an answer that cannot be piped as text (map, image marking, Q-sort) is also given in words below it.
   A started timed task (IAT, assignment, discounting) stops when the tab is hidden or someone chooses “Stop task”, not merely because a system notification briefly takes the focus.
   Even on a screen 320 px wide with double text size, left to right or right to left, everything stays on the page: long words wrap, and 2-D positioning then puts the name of the vertical axis above the field.
4. **End page**: thanks, the deletion code with a copy button, a link to the privacy page, results if the survey shows them.

![Live feedback: the participant as a figure in the crowd of earlier participants](img/crowd.jpg)

The participant pages are anonymous by default, use no external fonts or trackers, and work with keyboard and screen reader.
Every survey has a privacy page, an accessibility statement and a complaint form that need no account.
