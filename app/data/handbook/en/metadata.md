# Metadata, archiving and publication

Data are only as useful as their description. This page explains which description Askurious writes for a survey, where the details come from, what makes them comparable across studies, and how a survey is archived and published on Zenodo or Coscine.

## 1. Why metadata

Whoever wants to reuse data later needs to know what was collected, from whom, when, how and under which conditions. Without these details a data set is a table with column names. The FAIR principles sum this up: data should be **findable** (title, keywords, topic, a fixed identifier), **accessible** (a place and a licence), **interoperable** (standards that programs can read) and **reusable** (question texts, answer codes, sample, flow).

Askurious writes this description from what you enter anyway when creating the survey and in the research plan. You do not fill in a separate metadata form.

## 2. What Askurious describes and who reads it

Under Export › Documentation there are three files, and the same files are in the packages for Zenodo and Coscine. None of them holds answers.

| File | Standard | Who reads it |
|---|---|---|
| DDI Lifecycle 4.0 (XML) | Data Documentation Initiative, 4.0 public review draft | data archives and tools that process DDI Lifecycle; holds the whole questionnaire with its flow |
| DDI Codebook 2.5 (XML) | Data Documentation Initiative, version 2.5, after the CESSDA profiles | the CESSDA Data Catalogue and the European Question Bank (through the archives that report to them) |
| CDIF (JSON-LD) | Cross-Domain Interoperability Framework 1.1 (CODATA), schema.org | catalogues across disciplines, such as those of the European Open Science Cloud |

Both DDI files describe the study (title in every language, team, abstract, period, country, sample, sampling procedure, mode of collection, licence, funding, publications) and every column of the data set (question text, answer codes with missing codes, format). Mode of collection, time method, sampling procedure, unit of analysis and kind of data appear as terms of the DDI Alliance's controlled vocabularies, with the vocabulary and term address.

CESSDA does not accept studies itself; its catalogue collects the descriptions of the archives that belong to CESSDA. You can additionally check yourself whether a file meets the profiles in the CESSDA Metadata Validator (upload the DDI Codebook 2.5 file, choose the CDC or EQB profile).

## 3. Where the details come from

| You enter (assistant or settings) | In the metadata |
|---|---|
| Title, translations of the title | title and parallel title per language |
| Description | abstract; without a description the research question, without that the title |
| Research question | part of the study description |
| Keywords, topic from the CESSDA topic list | keywords and topic classification with the vocabulary address |
| Languages | languages of the study and of the data file |
| Research design, waves of a panel | time method (cross-section, panel, trend) |
| Recruitment, kind of panel or random sample | sampling procedure |
| Population, country, region | universe, country (ISO code), geographic coverage |
| Sample target, expected exclusions, expected response, quotas | planned sample |
| Team with ORCID and organisation | creators with organisation and ORCID |
| Funding: funder and grant number | funding |
| Licence in the consent | conditions of use |
| Publications under Export › Publications | related publications with DOI |

What Askurious sets itself:

- **Publisher:** the organisation of the person who created the survey, else the first organisation in the team, else that person's name. Not the operator of the installation: the researchers are the publisher.
- **Study number:** the survey's fixed identifier, which never changes, under the **agency** `de.askurious`. The agency is the namespace of all identifiers in a DDI file and is registered with the DDI Alliance, so an identifier from Askurious never clashes with one from another archive.
- **DOI:** once the survey is published on Zenodo.
- **Mode of collection, kind of data, unit of analysis, field period** from the kind of survey and the dates of its start and close.

The box **"For data archives"** on the overview of the setup assistant shows which of these details are there, which Askurious sets itself and which are still missing, each with a button to the step where it is entered.

## 4. Comparability

- **Validated constructs** appear as a concept on their variables, with authors, year and DOI of the source.
- **Fixed identifiers:** every question, variable and code list has an identifier that stays the same in every export; the version follows the questionnaire version.
- **The flow is machine-readable:** a condition appears in words and as an expression over the variables (for example `gender == 1`), "else" as its opposite, a randomisation as a split, a loop as a loop. In Codebook 2.5 every variable says under which condition it is asked.
- **Derived variables** come with their calculation: calculated values with the variables they come from, reverse-coded statements and the weight with its procedure.
- **Panel waves** appear as a series; each wave names the others by their label and study number, not by their title.
- **Batteries of statements:** the statement as the question, the stem before it, so that a question bank finds every statement on its own.

## 5. Archiving and publishing

Askurious uploads to **Zenodo** and to **Coscine** (Export). There is no connection to other archives; you can hand the DDI files to an archive yourself.

- **Zenodo:** a draft with title, team, description, keywords, licence and period, which you check and publish on Zenodo; that is when the DOI is made. A licence in the consent is needed (CC BY 4.0, CC BY-SA 4.0, CC BY-NC 4.0 or CC0 1.0).
- **Coscine:** the same files into a new folder of your Coscine resource, with the details of its profile.

The package holds the answers (data.csv), the codebook, the questionnaire as Markdown, the survey file, the survey material as PDF, both DDI files and CDIF, the constructs, the coding guide, the long formats, the tables of the loops, the images with documented rights, the seal chain and a README with checksums.

What never goes along:

- the wording of constructs without a general permission to publish it (only identifiers and values);
- the participation identifier and URL parameters;
- free texts, exact map positions, postcodes and exact dates, unless you tick that explicitly;
- images without documented rights; archiving is then blocked until they are cleared.

If the consent says that the data are not published, Askurious asks for an explicit confirmation before uploading.

## 6. Traceability

- **Pre-registration:** when data collection starts, Askurious freezes the survey as a ZIP with the survey file, questionnaire, codebook and survey material, with the SHA-256 of every file and a timestamp of the DFN; the checksum of the ZIP can be cited in a pre-registration.
- **Seal chain:** every participation that ended gets an entry whose checksum depends on the one before; changes and deletions are written as new entries. So anyone can check, also without Askurious, that no case is missing from the archived data set.
- **Checksums:** every package has a file checksums.sha256.

## 7. Conformance

The files conform without anything to do on your side. With every change to Askurious, tests check for every kind of survey (questionnaire, diary survey, interview guide), for a survey with only a title and for every study template that both DDI files are valid against their XML schema and that the Codebook 2.5 file holds every mandatory element of the CESSDA profiles CDC and EQB, as the download and as the file of a package. There is therefore no separate check in the interface; the box "For data archives" shows what is missing, as a recommendation.
