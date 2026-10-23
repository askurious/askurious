# Guides

The other pages of the handbook explain Askurious page by page. This page starts from the goal: each guide leads step by step through a typical study and points to the description of each page for the details.

---

## My first survey

This guide goes once through a whole survey, from creating it to the export. If you use Askurious for the first time, start best with a template: it brings a finished research plan, a questionnaire and 120 dummy participations, so every page already shows something.

**1. Create.** Under "My surveys" the button "Questionnaire" creates a new survey. The assistant offers three ways: start blank, from one of the six templates, or from a survey file a colleague exported, for example. Starting blank leads step by step through title, description, research plan, languages, consent and appearance. The **description** matters: it later appears in the consent under "What is it about?" and gives the AI support its context. More under [Creating a survey](build.md#2-creating-a-survey).

![The six study templates](img/new-from-template.jpg)

**2. Plan.** The tab [Research plan](build.md#5-research-plan) holds the research question in one sentence, the goal (describe, test relationships, compare groups, test an effect, measure preferences) and the hypotheses. A hypothesis names two questions of the questionnaire and the expected direction; "auto" picks the fitting test by the level of measurement. From the smallest effect you care about, the power analysis makes a sample target, e.g. "84 usable · 94 completions · at 10 % exclusions". A plan that stands before the data protects against setting up hypotheses only after looking at the results.

![Research plan: research question, goals, design and hypotheses with their tests](img/research-plan.jpg)

**3. Build the questions.** In the [questionnaire editor](build.md#6-questionnaire-editor) you drag element types from the left into blocks in the middle. Validated scales come ready from the [constructs](more.md#constructs), with wording, source and answer format; your own questions you write yourself. Every new questionnaire starts with the consent and a block with age and gender. The **pre-launch check** on the right says what is still missing before the start or should be checked. Saving happens by itself; "Undo" takes back every change.

![The questionnaire editor: element types on the left, blocks in the middle, pre-launch check on the right](img/editor.jpg)

**4. Consent and settings.** In the [settings](build.md#4-settings) you set the languages, the consent text, the privacy notice, the periods for deleting identifiers and the field end. In the consent text check above all the names, the contact and the purpose of the study.

**5. Test.** The [preview](build.md#10-preview-and-pretest) shows the survey as participants see it, at desktop or phone width. A **pretest link** lets colleagues click through without an account and leave a comment on every page, with a screenshot if they like. Test runs and dummy data never count for the quotas or results of real participations; with dummy data, analysis and export can be tried out before fieldwork.

![The preview of a Likert matrix with the comment field for pretest participants](img/preview-likert.jpg)

**6. Start.** On the tab [Data collection](field.md#11-data-collection), "Start survey" starts fieldwork, or "Schedule start" sets a time. Above it, "State against the power analysis" shows how many usable participations are still missing. Below are the participation link, a short name like `/s/mensa-studie` and a QR code. At the start Askurious records the research plan as a pre-registration with a timestamp.

![Data collection: the participation link with QR code, short name, links per group and panel connection](img/field-links.jpg)

**7. Assure quality.** [Quality assurance](field.md#12-quality-assurance) marks conspicuous participations: too fast (speeders), always the same answer, failed attention checks, bots. You decide whether marked participations drop out; this **basis of the analysis** then holds alike for results, dashboards, export and methods section. Best set the rule before data collection.

![Quality assurance: basis of the analysis and completion times with the speeder threshold](img/quality.jpg)

**8. Analyse.** The [results](analysis.md#13-results) test each hypothesis with the planned test, say "supported" or "not supported", give the sentence for the report and say what the test could have found with the actual n. Per question there are statistics and distributions, per scale α and ω.

![Results per hypothesis: test, result, sentence for the report and what the test could have found](img/results-hypotheses.jpg)

**9. Export.** The [export](analysis.md#15-export-and-open-data) writes the answers as CSV and for SPSS, JASP, jamovi and R, with a codebook. The **methods section** writes a draft for the report from the figures of the survey, and the data can be uploaded as open data to Zenodo or Coscine.

![Export: data pool, export of answers, methods section, formats](img/export.jpg)

---

## Panel study with quotas {kind:survey}

A panel provider sends people from its pool into the survey and rewards them for taking part. For that it needs three things: a participation link to which it appends each person's ID, completion links that bring people back to it at the end, and a view of the quotas. Links and IDs are on the tab "Data collection" in the block "Participation link" under **Panel connection**, the quotas under "Flow" › "Quotas".

**The quickest way.** The provider usually sends a list of its return addresses (complete, screen-out, quota full, quality, duplicate). Under "Paste links from the panel" this list goes in in one go. "Recognise" matches each line to an outcome by its words, puts the ID at the end of the address in as a placeholder and creates the parameter for the ID as "store, each value only once"; a preview shows everything before it is applied. Lines for answering too fast or for the country from the IP address are not applied by themselves, because there is no outcome of their own for them: they belong at an end in the flow with the matching condition.

![Paste links from the panel: the recognised list before applying it](img/guide-panel-paste.jpg)

**IDs from the participation link.** Every value the provider appends to the link (e.g. `?PID=4711`) must be set up as a parameter; other parameters are ignored. For each parameter you choose what happens to the value:

| Handling | What happens |
|---|---|
| do not store | The value holds only during the participation: for completion links, conditions in the flow, quotas and texts. It is not in the data. |
| store (pseudonymous) | The value is also in the data. The data are then pseudonymous, and the consent points this out by itself. |
| store, each value only once | as "store", and each ID takes part only once; a second call from another browser goes to the outcome "duplicate". This is the setting for the panel's ID. |

"Required" and a list of allowed values turn away links without the parameter or with another value: they show "This link is incomplete". The panel's ID should be required, or someone can open the link without it any number of times; the pre-launch check reminds you. Below the completion links is the finished **link for the panel** with all parameters, into which the provider puts its values where it says "…". Stored IDs are emptied by the setting "Delete identifiers and contact details after …" after the chosen period (new surveys: 6 months); that is enough for settling up with the provider.

![Panel connection: parameters from the link and completion links with the placeholders](img/guide-panel-params.jpg)

**Completion links.** For each outcome of a participation a link can be stored, to which Askurious sends the person at the end:

| Outcome | When |
|---|---|
| completed | The person has finished the survey. |
| screen-out | An end in the flow of the kind "Screen-out" (the person is not in the target group), also with the trigger "No sub-quota fits". |
| quota full | An end with the trigger "Quota full" or "Sub-quota full"; also every new call after the survey has closed because all quotas are full. |
| excluded | An end of the kind "Exclusion", e.g. after a failed attention check. |
| duplicate | The ID or this browser has taken part already; nothing new is stored or counted. |
| consent declined | The person did not agree to take part. |
| survey closed | The survey is taking nobody at the moment (paused, closed or after the field end). |

The buttons under "Insert placeholder" put values into a link that are filled in when the person is sent on: every parameter set up, under its name (e.g. `{{PID}}` for the panel's ID), the participation ID of Askurious, the language, the survey and, if allowed below, the code of the sub-quota (`{{quota}}`). So Askurious hands back the ID the panel sent, unchanged, and the provider knows whom to credit for the participation. Answers never go into a link. Only `https://` is allowed, and placeholders may only come after the host name; the target therefore always comes from the settings, never from the call. The privacy page of the survey names the targets of the redirect.

For each link you choose how the person leaves: redirected at once, via the end page with a button, or via the end page that moves on by itself after 5 seconds. An end in the flow can have a link of its own, which then applies before the link of the outcome. **Rules** pick another link per provider or per sub-quota, e.g. "quota full, if parameter `source` = b, then …". The first matching rule of an outcome applies before the general link; a rule without a link leaves the end page in place. With "duplicate", "consent declined" and "survey closed" there are no answers yet, so rules there test parameters only.

![A rule for a second provider and the link for the panel below it](img/guide-panel-rules.jpg)

**Quotas with a panel.** You set up quotas under "Flow" › "Quotas" (see [Quotas](build.md#8-quotas)). Their conditions can follow answers or a parameter the provider sends along, such as age or gender from its pool. Such a parameter needs a list of allowed values or a signed entry link, or anyone with the link can give themselves the value; the pre-launch check says so. Where the quota calculation stands in the flow decides when a place is taken; an end with the trigger "Quota full" after it sends everyone for whom there is no room to the completion link "quota full". A hard quota fits when the panel charges for every participation. Whoever is taking part keeps their place for the hold time; broken-off participations give it back. As an admin you can change target, type, tolerance and hold time during fieldwork and close single sub-quotas. Some providers want no automatic filtering during the survey. That is why participations marked by the quality assurance fill their place as long as the tick "Participations marked by the quality assurance fill no quota place" is off.

![The quota state, filtered to one provider's participations](img/guide-panel-quota-state.jpg)

**Quota state for the provider.** The box "Quotas" shows per sub-quota the target, completed, running, open and refused, from real participations only. If several providers work in one survey, each gets a value of its own in a stored parameter (e.g. `source=a` and `source=b` in their participation links); the state can then be filtered to one provider's participations. The state can be copied as a table (for an email or the provider's portal), downloaded as CSV and opened as a print view. Under "Links" you create a link that shows this overview without signing in and read-only, if you like only with one provider's participations and without the conditions of the sub-quotas, for a limited time or until revoked. It shows every completion at once; with small sub-quotas a participation just completed can thus be assigned to its sub-quota. So only pass it on where the numbers are needed for steering. A [dashboard](analysis.md#14-dashboards) can get a link for a provider too: it shows the key figures of the data collection and the quotas from that provider's participations only, in steps rather than exactly, and leaves out tiles with results of questions.

![The quota overview a provider sees through their link](img/guide-panel-quota-link.jpg)

![A dashboard link for a provider: "Shows the participations of"](img/guide-panel-board-link.jpg)

**Once-only IDs.** An ID counts as a "duplicate" only once its earlier participation has ended or gone past its first page. If someone sent only the first page (the consent) with the ID and has not gone on for 10 minutes, the next person with that ID takes part; the earlier participation goes on without the ID. So nobody who knows someone else's panel ID can lock the real person out with a single page. In the same browser a started participation goes on as usual.

**Signed links.** Some providers require a completion link to carry a signature so that nobody can call it by hand. Under "Signed links" you enter the key the provider issues and choose method, encoding and scope as it names them; `{{sig}}` puts the signature into the completion link. An example with test values helps to compare with the provider.

Only a **signed entry link** prevents made-up IDs and self-chosen values for quotas (tick "Have the entry link signed too"). Instructions for the panel: it appends the signature to the entry link as its last parameter (name adjustable, default `sig`), e.g. `https://…/s/abc123?PID=4711&sig=…`. It signs, with the same key, method and encoding as the completion links, the address as called before this parameter (with or without the `&` before it, as chosen under "Signed is"); the signature over only the part after the `?` (`PID=4711&`) is accepted too. All URL parameters of the survey must come before the signature. A link without a valid signature shows "This link is incomplete", the plain participation link too. If quotas or conditions follow URL parameters without a list of allowed values and without a signed entry, the pre-launch check says so.

**Before the start.** Preview and test runs count for no quota. Try the link for the panel with a test ID and have the provider confirm that the returns arrive with the ID; the pre-launch check reports faulty completion links and rules.

---

## Online experiment {kind:survey}

An online experiment assigns participants at random to conditions, for example two versions of a text, and then measures the same thing in everyone. Because chance alone decides who sees which condition, a difference between the groups can be read as an effect of the condition. The template "Online experiment with two conditions" shows the whole set-up and is a good starting point.

**1. Plan and sample.** In the [research plan](build.md#5-research-plan) choose the goal "test an effect" and the design "experiment". The hypothesis names which condition is to have which effect, such as "Whoever reads text B rates the offer better". The power analysis asks for the smallest effect you care about and gives the number needed per group. Groups of equal size give the most power.

![Research plan of an online experiment: hypothesis with test and sample target](img/guide-experiment-plan.jpg)

**2. Build the conditions.** Each condition goes into a block of its own in the questionnaire, for example "Text A" and "Text B". The measurement after it stands in a shared block that everyone sees: the same questions in the same order, often a validated scale from the constructs. A check question on whether the condition came across (e.g. "How positive was the text?") helps with the interpretation later. In a Likert matrix, "Include an attention check" adds a statement with a prescribed answer.

**3. Assign at random.** In the [flow](build.md#7-flow) drag a **randomisation** before the measurement block and put the condition blocks into it. It shows each person one of the blocks, either fully at random or **balanced**: balanced fills the least shown condition first and so keeps the groups the same size even with few people. Askurious decides the assignment once per participation on the server and stores it; reloading and "Back" do not change it, and the export holds it as a column of its own.

![The flow of an online experiment: consent, a randomisation between two texts, then the same scale](img/flow-experiment.jpg)

**4. Test.** In the preview you jump between the pages and see each condition. Dummy data go through the real flow and spread over the conditions like real participations; so you see before fieldwork what the results page will look like. Test runs and dummy data never count among the real data.

![One of the two conditions in the preview](img/guide-experiment-preview.jpg)

**5. Analyse.** The [results](analysis.md#13-results) test the hypothesis with the planned test, with two groups usually Welch's t-test, and say "supported" or "not supported", with the sentence for the report. Below it stands which effect the test could have found with the actual n; so "not supported" with too small a sample is not read as "no effect". Every question can be split by the experimental condition.

![Results of an online experiment: Welch's t-test for the hypothesis and the distribution of answers](img/results-experiment-a.jpg)

**Variants.** If everyone is to see several conditions, or there are several factors with levels (a vignette study), a **loop** in the flow builds the design: per person k of N rows of a table, or combinations of factor levels. That saves people but risks order and learning effects. The template "Vignette study with 2 × 3 factors" shows such a set-up.

---

## Seminar with own links per person {kind:survey}

In many seminars the students recruit participants themselves, for example among friends. With a link of its own per person, you and the students see how many took part through each link. The data hold only a random code of the link, no name.

**1. Create the links.** On the tab "Data collection", open in the block "Participation link" the part **"Own links for people or groups"**. Enter the names, one per line, for example copied from a spreadsheet, or just a number if the names come later. Each line gives one link with a random code like `…?w=k7mp2q`. Only you see the names on this page; they go neither into the link nor into the data.

![Creating own links: one name per line and the tick for dashboard links](img/guide-seminar-add.jpg)

**2. A dashboard link per person.** With the tick **"Create a dashboard link for each person"** each person also gets a link to a dashboard that shows only the number of their own participations. So the students can look up themselves how far they are, without access to the survey. Next time Askurious uses the same dashboard again.

**3. Hand them out.** The links come as a list to copy and as **QR cards to cut out**, one card per person. If the platform sends emails, "Send the links by email" sends each person their link; the addresses are not stored.

![QR cards to cut out, one per person](img/guide-seminar-cards.jpg)

**4. Count.** The table shows per link how many people started and completed and how many of them are **usable**, that is not marked by quality assurance. The column "Their status" holds the dashboard links; "Download the list (CSV)" and "QR cards to cut out" are below. If a link says "conspicuous", that only means: take a closer look, for example when many completions come shortly after one another or many are marked; "What the numbers mean" explains it. IP addresses are not used for this.

![The table of own links with the participations per link](img/guide-seminar-table.jpg)

**5. What the students see.** The dashboard link shows only their own number, no answers and no numbers of the others.

![A person's dashboard: only the number of their own participations](img/guide-seminar-dashboard.jpg)

**6. Limit.** Under **"Maximum per link"** you set how many people may at most complete through a link, e.g. 10. When a link is full, further people see the end "Quota full". For this Askurious creates a quota with one part per link; before the start it can be created that way, afterwards only the number can change.

**Good to know.** An own link does not stop someone from taking part twice; for that there is the setting for taking part in the same browser. And the code in the link does not make the data personal: only someone with this page's list of names can assign a code to a person.

---

## Longitudinal study over several waves {kind:survey}

A longitudinal study asks the same people several times, for example before and after a seminar or once per term. To merge a person's answers over the waves without storing names or email addresses, Askurious uses a **personal code**.

**1. Build the first wave.** Create the first wave as an ordinary survey. Add the element **"Personal code"**, best early in the questionnaire. It builds a code from 3 to 6 short questions people answer the same way every time, for example the first letter of their mother's first name or the day of their own birthday. Each part has a field of its own with a check and "don't know". With small groups take fewer parts so that nobody can be recognised by the code.

![The personal code as participants see it](img/guide-waves-code.jpg)

**2. Create more waves.** Each wave is a survey of its own. Wave 2 is easiest made with "Duplicate" under "My surveys": it then has the same personal code with the same parts and the same scales, and you change only what is to differ.

**3. Link the waves.** On the tab "Research plan" the section **"Waves"** stands below the plan. There you choose the surveys of the single waves and give each a name, such as "before" and "after". The list holds for all waves in it. Linking is no part of the plan itself: adding a wave later is no deviation from the pre-registration. Also in the research plan you set which variables are modelled over time; a hypothesis of the kind **"effect over time"** (X in one wave on Y in the next) is tested in the cross-lagged panel model.

![The section "Waves" in the research plan with three linked surveys](img/guide-waves-plan.jpg)

**4. Merge.** On the "Export" page of each wave, **"Merge waves"** merges the data by the code: wide (one row per person, the columns of each wave with `t1_`, `t2_` …) or long (one row per person and wave), as CSV, .sav or .rds, plus a matching report. Codes that occur twice in one wave are not linked. With "tolerant" a code that would otherwise stay alone is also linked to a code that differs in exactly one part, but only where that is unique both ways; the column on the quality of the match says so.

**5. Analyse.** The results of each wave show the [longitudinal analysis over the waves](analysis.md#13-results): means per wave, measurement invariance over time, growth curves and the cross-lagged panel model, from three waves on the RI-CLPM. It is computed with the button "Compute longitudinal analysis" over all linked people. Measurement invariance and the growth curve need at least 100 linked people with a value, the cross-lagged panel model at least two variables. The page computes the RI-CLPM from three waves and 200 linked people, otherwise the simple model with a note.

![Longitudinal analysis over the waves in the results](img/guide-waves-results.jpg)

**Good to know.** The code links only people who remember their answers. Expect some people who cannot be linked and plan the sample larger accordingly. Short prompts over days or weeks rather than a few large waves fit a [diary survey](kinds.md#17-diary-survey) better.
