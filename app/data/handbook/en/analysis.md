# Analysis

This page describes the results, the dashboards and the export.

## 13. Results

![Results per hypothesis: test, result, sentence for the report and what the test could detect](img/results-hypotheses.jpg)

**What it is.**
The results of the basis chosen above, live while data come in:

- **Results per hypothesis**, from the research plan: the test and why it was chosen, supported or not, the sentence for the report in APA style, and **what the test could detect** with the actual n (a sensitivity analysis), so that "not supported" is not read as "no effect" when the sample was too small.
- **Per element**: n, M, SD, median, confidence intervals, distributions; for scales Cronbach's α and McDonald's ω with intervals, and an item analysis.
- **Scales at a glance**: all scale scores with α, ω and their correlations, as a table for manuscripts (Word, German or English).
- **Split into groups**, e.g. by experimental condition; with random assignment the difference can be read as an effect.
- **Choice models**: counts, aggregate logit and hierarchical Bayes (HB) per person, importance and part-worths, willingness to pay, segments (latent class), a market simulator.
- **Longitudinal analysis over the waves** (with linked waves): means per wave, measurement invariance over time, growth curves and the cross-lagged panel model (from three waves the RI-CLPM), computed on a button over all linked people; visible with read access to every wave.

![Scales at a glance: scale scores with α, ω and correlations](img/results-overview.jpg)

![Results of an online experiment: Welch's t-test for the hypothesis and the distribution of answers](img/results-experiment-a.jpg)

![Results of a conjoint: importance of the attributes and part-worths](img/results-conjoint-a.jpg)

| Choice | Trade-off |
|---|---|
| Which test | Follows from the level of measurement: Pearson for two metric variables, Spearman when one is only ordered; Welch's t-test or Welch's ANOVA for groups, which do not assume equal spreads (Delacre et al., 2017); rank tests for ordinal points; χ² for two nominal variables. |
| Aggregate logit or HB | The logit describes the sample as a whole in seconds; HB estimates values per person, which shows how different preferences are and feeds the simulator, but takes thousands of draws. Importances can differ markedly when people rate an attribute in opposite ways. |
| Number of segments | The BIC suggests a number; also choose by content: segments that differ clearly, can be named and predict holdout tasks better. |
| What is weighted | Shares, means and their intervals are weighted; counts, reliability, scale scores and choice models stay unweighted. |

**Why.**
Results should be correct by default and readable by someone who is not a statistician: the test is chosen from the plan, every figure has a question mark that leads to the glossary of statistics, and every page lists its references.
Reliability is reported as α and ω with intervals (Hayes & Coutts, 2020), because α alone assumes that all statements measure equally well.

---

## 14. Dashboards

![Dashboards: a field report, a results report or an empty one](img/dashboards.jpg)

**What it is.**
Pages with results, quotas and key figures of the data collection that can be passed on by link, without an account: to a client, a project partner or a lecture hall.
Templates: field report (participations, course and quotas), results report (all questions), empty.

**Why.**
A dashboard shows only summarised figures: no free texts, no answers of individual people, results only from 10 answers (adjustable per dashboard), and answers named fewer than 5 times are combined.
While the survey runs, a link shows new figures only after every 5 further participations, so that one new participation cannot be read off the change.
The trade-off is less detail for the reader in exchange for protecting the participants.

---

## 15. Export and open data

![Export: data pool, export of the answers, methods section, formats](img/export.jpg)

**What it is.**
Everything that leaves the platform:

- **Answers** as CSV (comma or semicolon), SPSS/JASP (.sav), jamovi (.omv), R (.rds), with a **codebook**; optionally with scale scores.
- **Questionnaire** as Word (codebook or print version), constructs and **references** (.bib, .ris).
- **Data flow** as Word: from consent to deletion, per step the data, measures and people responsible, filled from the settings of the survey (identifiers, separate details, paradata, AI coding, outside services, periods, open data); for the record of processing activities or the data protection officer.
- **Survey file** (.json/.zip) to move or share the whole survey; from the role “Edit” on, as duplicating.
- **Results report** as PDF, made without AI, after APA 7: all computed results of the saved data pool with the data basis, contents, sample, numbered figures and tables per question (number and title above, note below), quotas, hypotheses of the research plan, scales, choice experiments with segments and market simulator, structural equation and panel models and the cited references; free texts are not in it.
- **Survey material** as PDF, made without AI: part 1 shows the questionnaire as participants see it, numbered; part 2 the same with coloured notes beside each element (display logic and conditions, randomisation, required answers and quality checks, variable names, sources), with what a condition or randomisation holds indented below it. The PDF carries the logo and accent colour of the survey's design, such as an organisation design; the colours of the notes always stay the same. The pre-registration freezes it too; Zenodo and Coscine get it without protected instrument wording.
- **DDI Lifecycle 4.0** as XML, for data archives and catalogues: the survey after the standard of the Data Documentation Initiative (4.0 public review draft), with the team and description, field period and mode, the questionnaire with its blocks, conditions and randomisation, the questions in all languages, the answer codes and every variable of the data set. Mode, time method and sampling procedure appear as terms of the DDI Alliance's controlled vocabularies, as far as the research plan and the settings name them, as are the population, country, region and the details of the sample; an open field stays empty. No answers, so also with the role “View”.
- **CDIF** as JSON-LD, for catalogues across disciplines: the data set after the Cross-Domain Interoperability Framework (CDIF 1.1 by CODATA) with title, team and ORCID, licence, period, country, mode and every column as a variable with its answer codes. The Zenodo and Coscine packages hold both files, DDI and CDIF, without protected instrument wording and only with the columns the package has.
- **Pre-registration**, frozen and time-stamped at the start of data collection: survey file, questionnaire, codebook, survey material and checksums; its checksum is also in the seal chain. Below it the comparison shows whether the questionnaire in the field has changed since it was frozen, and what.
- **Seal chain** as a ZIP with a stand-alone verification program, and a check code with a public page that anyone can open without an account, for example [the demo survey's chain](https://askurious.de/verify/42-6-f5997ff3715b31a9): 5 cases, one of them deleted with a deletion record.
- **Open data**: upload to Zenodo (as a draft; publishing is a separate, confirmed step) or Coscine (create project and resource, store, check each upload in Coscine, on request with SHA-256, see all folders of the survey in the resource, invite co-researchers into the Coscine project and request publication). In the editor, images of an element and tables (drill-down, “Paste a list”) can be taken from a Coscine resource with “choose from Coscine”; they are copied, “take again” takes the file anew. With the experiment “Take video and sound from Coscine” this also works on the element “Video / audio”; the file is loaded in the background. When creating “from a file”, a survey file (.json, .zip) can be chosen from Coscine; it goes through the same preview as an upload.
- **Deletion requests** from participants who contact the researchers directly.

**How the page is laid out.**
At the top is “Data pool and format”: data sources, how marked participations are handled, appended scale scores and the separator, all in view, with one line on the export selection below. Then cards in two columns: **Answers**, **Documentation**, **Sharing** (survey file, data access for R and Python), **Evidence** (pre-registration, seal chain, saved states), **Archive and publish** (Zenodo, Coscine, image rights, publications) and **Data protection** (retention, deleting answers, separate section). Each content has one row with its formats as buttons; the “i” explains it in more detail, and whatever needs more than a button (tokens, seal chain, Zenodo, waves) opens in a dialog. Live poll, interview guide and diary survey show the same cards where they fit.

![The methods section: field period, people, times, demographics and validated instruments from the survey's own figures](img/methods.jpg)

The **methods section** collects the facts of the survey (field period, people, exclusions, completion times, demographics, validated instruments with α and ω, hypotheses and tests) and writes a draft in German or English, as a Word file, with or without AI.

While a report is being prepared, its waiting page names the survey and the last confirmed update. After a connection failure it checks the same job again; “Check progress” checks immediately. Links return to Field and Export, including when the calculation fails.

**Why.**
Open data should be a few clicks, not a project.
The export always contains the source column, recodes reverse-keyed statements, reports randomised orders as `*_order` columns and never includes previews (decision of 2026-09-26).
The page says whether something has been deleted since the last download, so that a published file can be replaced.
