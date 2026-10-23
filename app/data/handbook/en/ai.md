# AI in Askurious

Askurious uses AI as an assistant for the researcher, never as a participant, an analyst or a decision-maker.
This page lists every place where AI is used, what it receives, what it produces, how its output is labelled, and what AI deliberately does not do.

## The rules in short

1. **The AI suggests, you decide.**
   Every AI function runs only when a researcher clicks a button marked "with AI".
   It returns suggestions, often with a reason; the researcher ticks, edits and accepts what fits, as one step that can be undone.
2. **No participant data goes to an AI.**
   No AI function receives the answers of participants.
   The methods section receives aggregate figures only (n, means, α, test results), never single answers.
   Interviews, recordings and transcripts never go to an AI provider.
3. **AI output stays visible as AI output.**
   AI-drafted scales are marked "AI-generated, not validated" in the editor, the element page, the pre-launch check and the codebook.
   AI translations stay "AI, unchecked" until a person confirms them.
   AI images carry their source, model and prompt into the export.
4. **AI never looks validated.**
   AI items carry no authors, references or scale names, so that they cannot be mistaken for a published instrument.
   Their reliability has to be checked in a pretest.
5. **Researchers stay responsible.**
   The terms of use say: you check AI results yourself, and you do not enter personal data into AI functions.
   Where a free text field goes to the AI (live poll, interview guide), the page says so next to the field.

## Who provides the AI

- **Text and images**: OpenAI, through its official interface.
  The platform admins choose the text model and the image model in the server settings.
  What is typed into an AI function is sent to OpenAI for processing; the terms of use (section 6, "Third-party services") name this.
- **Speech to text**: a Whisper server run by the institution itself (or on its behalf), not OpenAI. The optional local WhisperX service supports automatic speaker labels. They apply only within one recording part and need checking and assignment to roles.
- With the local WhisperX service, select `small` or `large-v3-turbo` per interview before starting transcription (`large-v3-turbo` is preselected: more accurate, but slower). The interview list shows queue position, processing phase and completed recording parts; you can also cancel there. Changing the model transcribes the parts again. Corrected text is kept; a new draft is offered alongside it.
- The approximate remaining time covers the current recording part including known waiting time, rather than all subsequent parts. It is shown as a range estimated from audio duration, model and previous runtimes of each processing phase. Completed phases are excluded from the time remaining. The display explains when information is missing or processing takes longer. If a job reaches its memory limit, choose `small` where appropriate or split the recording into shorter files; completed parts are retained when keeping the same model.
- Following an interruption due to server load or a restart, the local service resumes the same job from the last completed processing phase. After a processing failure with an existing checkpoint, it tries this once automatically; memory and time limits are not retried automatically. Intermediate transcripts are deleted on completion, terminal failure, cancellation or expiry. A subsequently submitted new job starts afresh.
  Audio recordings of interviews therefore never leave the institution's infrastructure.

Each researcher has a daily limit of AI texts and images (default 100 texts and 200 images).
Every call is counted for the admins' overview of usage and costs, without the person, the survey or the content.
AI buttons share one light purple colour and a sparkle icon, so that AI actions are recognisable at a glance.

## Where AI is used

| # | Function | Where | What AI produces | What is sent | Labelling and control |
|---|---|---|---|---|---|
| 1 | **Write with AI** (consent) | consent element | a draft of the participant information and consent from the survey's settings | the facts of the survey; names, institutions and the contact email of the researchers are replaced by stand-ins before sending and put back afterwards | "AI draft inserted. Please check carefully: you are responsible for the text." The AI may only rephrase given facts; missing facts become [placeholders], and the pre-launch check blocks the start while any remain. |
| 2 | **Construct ideas** | editor, side bar | three to five constructs that the survey may still be missing, without scale names or references | title, project, description, research question, an outline of the questionnaire; no answers | suggestions only; needs some context (about 20 words) |
| 3 | **Draft a scale with AI**, **suggest more items** | editor | Likert statements for a construct idea, in all survey languages | the same context; no answers | "AI-generated, not validated" in editor, element page, pre-launch check and codebook |
| 4 | **Check element with AI**, **have texts checked by the AI** | element page; pre-launch check | wording suggestions with reasons, based on rules of questionnaire design (Porst; Dillman; Tourangeau) | the participant texts in the source language; not the consent, not locked constructs, not translations | each suggestion is ticked and accepted by hand, only in a draft |
| 5 | **Generate images with AI** | image element, loops, conjoint, answer options | stimulus images from a style, a scene and fixed rules (no text, no logos, no emotional framing) | the image prompt only; names and definitions of attributes are left out | new images must be approved before the survey can start; one image style per survey (or "No AI images"); source, model and prompt go into the export |
| 6 | **Have the prompts checked by the AI** | conjoint images | a clearer base prompt and image hints | only what the image model sees | accepted by hand; existing images keep their prompt |
| 7 | **Translate everything open with AI** | Translation tab | translations of missing texts | title, description and research question as context, plus confirmed translations as a glossary | "AI, unchecked" until a person confirms or edits; in a running survey it asks first, because participants see the result at once |
| 8 | **Methods section with AI** | Export › Methods section | the methods section in German or English, in three lengths | aggregate figures only: n, field period, exclusions, α and ω, means, hypotheses, test results | "Draft without AI" is offered alongside; it builds the text from the same facts without any AI |
| 9 | **AI help for vignettes** | vignette design | suggested factors, levels, implausible combinations, vignette text | study context and questionnaire outline; no answers | what is accepted is marked "AI-generated", because the choice of factors needs a theoretical reason |
| 10 | **Suggest questions** | live poll, first step and Questions | five feedback questions for an event | the presenter's description and the existing questions | note on the page: no names or other personal data |
| 11 | **Develop the guide with AI**, **add to a guide question**, **check the guide**, **category definitions** | interview guide | steps of Helfferich's SPSS method (collect, check, sort, subsume), aspects, a review, definitions for coding (Kuckartz) | title, research question, target group, mode, guide texts and notes; never interviews | every step can be skipped ("Continue without checking"); note: no names or personal data |
| 12 | **Transcribe** | interview guide › Interviews | a transcript draft following Dresing & Pehl, with time marks | the recording, to the institution's own Whisper server, not to OpenAI | "Draft by Whisper"; automatic speaker labels are possible with WhisperX, roles are assigned while checking; "Transcript checked" marks it as checked, and the recording is then deleted |
| 13 | **AInstein** (experimental) | own page, switched on per person by an admin | a conversation that fills a research canvas (question, design, sample, hypotheses, measures, analysis) and builds a survey from it | project name, idea, canvas, conversation, public constructs; no answers, no participant data | badge "AI" and "AInstein can be wrong"; everything it builds is marked "AI-generated" |
| 14 | **Have categories proposed (AI)**, **Have an assignment suggested (AI)** (experimental) | Results › Categorise | categories with a definition and a paraphrased anchor example; fitting categories for the answers still open | up to 150 distinct answer texts of participants (at most 300 characters each), real participations only, without other details; only over an own AI access (Account › Connections), never over the platform's access | only after the tick "Participants were told about the transfer" per survey, with a paragraph for the consent; suggestions only count once accepted, bulk acceptances are named in the coding guide and the methods section |
| 15 | **AI chat** (experimental) | element in the questionnaire or a diary module, switched on per person by an admin | the answers of an assistant in a conversation with participants, following the researchers' instructions | the participants' messages with the conversation so far and the instructions; e-mail and web addresses and phone numbers masked first, no identifier of the participation; only over the study's own model access (page “AI chat: model access”), never over the platform's access or a personal one | fixed note “You are writing with an AI” and “AI” at every answer; a tick of its own in the consent; all messages of both sides stored as study data; origin of the model and instructions in the survey's documentation; hint to help on crisis words |

### Screenshots

![Draft a scale with AI: the idea, the number of statements, half of them reverse-keyed; the note says what is sent and that the result is not validated](img/ai-scale-dialog.jpg)

![Translation with AI: texts stay "AI, unchecked" until a person confirms them](img/translation.jpg)

![The element page of a conjoint, with "Check element with AI" at the top right](img/element-conjoint.jpg)

![Live poll: the description goes to OpenAI's text model, with the request not to enter personal data](img/live-assistant.jpg)

![Interview guide: develop with AI, step "Collect"; the AI adds to what the researcher collected first](img/guide-ai.jpg)

![Choice tasks with AI-generated product images, all in one style](img/cj-ai-images.jpg)

![The image style of a survey: all AI images of a survey in one style, so that stimuli stay comparable](img/image-styles.jpg)

[Try a choice task with AI images yourself](https://ask.askurious.de/s/nubv72): a short demo survey of about one minute.

## Why AI is used this way

**Suggestions, never decisions.**
Methodological choices need reasons that the researcher can defend: which construct, which factors, which wording.
An AI can propose and point out problems, but it cannot carry that responsibility.
Hence the same pattern everywhere: the researcher asks, the AI suggests, the researcher accepts.

**Keep the stimulus clean.**
AI images get only image hints, never what participants read, and fixed rules against text, logos and emotional framing.
Differences between the conditions of an experiment should come from the manipulated content, not from the model's whims.
One style per survey keeps images comparable; images are generated, checked and approved before the start, never while a participant waits.
Images of levels such as brands are upload-only, because an AI would invent logos (decision of 2026-10-06).

**Keep validated and generated apart.**
A scale drafted by an AI may be a good start, but it is not an instrument with known reliability and validity.
The label "not validated" follows it into the codebook and the data package, so that readers of a study know what was measured with what.

**Keep people out of the prompt.**
The consent draft replaces the researchers' names before sending; free text fields warn against personal data; interview material is never sent.
Participants' answers only go to the AI when researchers have categories or an assignment suggested while categorising open answers: then only the answer texts of real participations, after confirming that the participants know about it.
The one AI that touches recordings (transcription) runs on the institution's own server.

**Learning, not shortcuts.**
In the interview guide and in AInstein the AI works step by step, after the researcher's own first attempt, because collecting and checking questions is where researchers notice their own assumptions (Helfferich, 2011).
AI mock interviews and AI-written consent for interviews were deliberately left out.

## What AI does not do in Askurious

- It does not answer surveys or code interviews; it does not assign open answers itself but only makes suggestions when categorising, which researchers accept.
- It does not decide which participations are excluded; quality assurance uses transparent rules (speed, patterns, attention checks).
- It does not run in the background or while participants take part.
- It does not publish or translate anything without a person confirming it.

AI is also something Askurious defends against: the optional **bait for AI agents** puts a sentence on participant pages that people neither see nor hear, and flags participations by AI agents that follow it (see [Building a survey](build.md#4-settings), "Paradata and data quality").
