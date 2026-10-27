# Other kinds of survey

Besides the questionnaire, Askurious has the diary survey, the interview guide and the live poll.
Each has tabs of its own; what they share with the questionnaire is under [Building a survey](build.md).

## 17. Diary survey {kind:diary}

A diary survey asks the same people several short times over days or weeks, in the app or in the browser.
It has the tabs of a questionnaire plus the tab "Schedule"; in the tab "Data collection" participants join with a code.
It is created with the same assistant as a questionnaire, step by step: **What is it about?** (title, description, keywords, topic, language, what is created along with it), **Who takes part?** (selection, population, country, region), **Responsibility**, **Schedule and joining** (one shared code or personal codes; when people are asked is set afterwards on the tab "Schedule"), **Consent**, **Appearance** and the **Overview** with the box "For data archives". Every step saves at once; whoever leaves the assistant comes back with "Continue setting up" on "My surveys". With AI on, the overview and "Plan with AI" on the tab "Schedule" lead to the diary's AI assistant: **Design** (scheme, prompts per day, length, after Bolger and Laurenceau, 2013), **Moment questions** (short questions about the moment, where one fits a construct with few items) and **Check** (burden, reactivity, context, entry and exit survey). Every step can be skipped; only what you tick is taken over.

### Schedule of a diary survey {kind:diary}

Instead of a single run, a diary survey has a **schedule**: the period per person (a fixed number of days or without end), the start and the **short surveys**, at fixed times, at random within time windows or started by the participants themselves in the app.
The questions of each short survey are a section of their own in the tab "Questionnaire".
Once the study is running or has real answers, short surveys, triggers and days are fixed; times, weekdays and texts can still be changed.
In the results a multilevel model tests relationships of two variables of the entries (within and between people, optionally lagged or with a random slope, set in the research plan); the scales of the short surveys appear as a two-level factor analysis with ω within and between people.

### Joining and participants of a diary survey {kind:diary}

In a diary survey the tab "Data collection" shows the block **Joining** in place of the participation link: how participants join (one shared code for everyone or personal codes), the join code with QR code, the test codes and the personal codes.
Below it are the **participants** with status, period, entries and answered prompts, on several pages when there are many (10, 20 or 50 rows per page); there a person's participation can be paused or ended.
The tab "Schedule" holds only the times.

---

## 18. Interview guide {kind:interview}

![The guide: topic blocks with guide questions, notes and stimuli](img/guide.jpg)

**What it is.**
A survey type for guided, qualitative interviews, with its own tabs: Settings · Guide · Interviews · Results · Export.
It is created with the same assistant as a questionnaire, in four steps: **What is it about?** (title, description, keywords, topic, research question, language, template and order), **Who takes part?** (target group, selection, population, country, region, interview mode), **Responsibility** (the organisation of the people involved, for the list of authors and the publisher in data archives) and the **Overview** with the box "For data archives". From the overview you go on to the guide, or with "Develop guide questions with the AI assistant" to the AI assistant.

- **Guide**: topic blocks, each with **guide questions** in four parts after Helfferich (2011): a narrative prompt read out, content aspects to tick off, maintaining questions and specific questions; plus notes, stimuli (images), a short questionnaire before and a postscript after the conversation (Witzel, 2000).
  Templates: empty, problem-centred interview, expert interview, narrative interview.
- **Develop with AI** (optional): the SPSS method of Helfferich: collect, check, sort, subsume.
- **Interviews**: start an interview with an identifier, date, mode and consent; audio recording if allowed.
- **Interview view**: the guide for the tablet or laptop during the conversation, with a clock, ticks per aspect and jumps between topics; without a stored interview it is a "trial without saving".
- **Transcription** on the institute's own Whisper server, never at a third party; speakers are set by hand, and the recording is deleted after the transcript has been checked.
- **Export**: Word, and REFI-QDA (.qdpx) for MAXQDA, ATLAS.ti and NVivo.

![Develop the guide with AI: step 1, collect](img/guide-ai.jpg)

![The interview view during a conversation](img/guide-conduct.jpg)

![Starting an interview: identifier, mode, consent, recording](img/guide-interviews.jpg)

**Why.**
Interviews need the same care for consent, storage and export as questionnaires, so they live in the same tool.
The AI does not write the guide at the push of a button: collecting and checking the questions is where researchers notice their own assumptions (Helfferich, 2011), so each AI step is a suggestion that can be skipped.
Names are kept out: the identifier is the only link to a person, and the list of identifiers stays with the researchers outside Askurious.

### Settings of an interview guide {kind:interview}

Title and research question, the researchers involved and the interview itself: target group, interview mode, order of the topic blocks, retention of recordings and data.

### Guide {kind:interview}

The topic blocks with their guiding questions, notes and stimuli, plus the short questionnaire and the postscript; "Develop with AI" leads through the steps after Helfferich (2011).

Follow-ups that fit only sometimes become **sub-questions**: "+ Sub-question" adds one to the selected guide question, the button → indents a guide question under the one above, ← brings it back. A sub-question stands indented with ↳ under its guide question in the guide, in the interview view (on the same page), in print, in Word and in the category system; "When to ask?" says when it fits, for example "if applicable". When dragging, a guide question becomes a sub-question if it lands between a guide question and its sub-questions or between two sub-questions, or if you drop it onto the middle of another guide question (the row is framed, "insert as a sub-question"); its own sub-questions come along. At the top or bottom edge of a row it is only reordered.

The copy icon on a guide question duplicates it with its sub-questions and tasks, right below; on a sub-question, a task or an element of the short questionnaire only that one. In the head of a topic block it duplicates the whole topic block. “Undo” takes it back.

**Specific questions** can be linked and tied to conditions too. The button → before a specific question indents it as a **follow-up** under the specific question above, with its own "When to ask?"; ← brings it back. In the interview view a specific question with follow-ups has the switch "applies / does not apply": with "does not apply" its follow-ups are paled. "only if" ties a specific question to an aspect of the guide question ("“Breaks” did not come up by itself": it is paled as soon as the aspect is ticked) or to an answer in the short questionnaire, a **case feature**, or to an earlier specific question of the same guide question ("Q1 applies" or "Q1 does not apply"; the specific questions are counted without their follow-ups). A sub-question can do the same ("Ask only if …"), with the specific questions of its guide question. A specific question that a condition names gets the switch "applies / does not apply" in the interview view too; while it is open, nothing is paled. The case features a page asks about stand at its top to tap; for a stored interview the answer goes into the short questionnaire. Nothing is ever hidden; you decide in the conversation. So that you see during the conversation what belongs where, the sub-questions in the interview view hang from a line in the colour of interviews under their guide question, follow-ups from a thinner line under their specific question. The specific questions carry their number Q1, Q2, … in front, and above every question with a condition the condition stands in the words of the switch, such as “only if Q1 applies”, followed by its state: “met”, “still open” or why it is not needed. A question that is not needed is paled; the line with its condition stays readable. With **A− / A+** at the top of the interview view you make the text of this view only larger or smaller (90 to 160 %); the device remembers the size, and the text size on “Account” stays for all other pages. The bar with the clock keeps its size, and with large text columns and topic blocks stack earlier so that nothing overlaps. Print and Word show follow-ups indented and the conditions as a note, the case overview has a column "applies" for each specific question with the switch. In the interview view the aspects and the maintenance questions stand side by side, the specific questions below them across the full width.

A **task** lets the interviewed person do something in the conversation and then talk about it: put terms in a **ranking**, sort cards (**card sorting**, closed with given piles or open with piles they name), rate statements (**Likert rating**) or choose an image (**image choice**). "+ Task" under a topic block adds it under the selected guide question, otherwise at the end of the topic block; it belongs to the guide question above it, stands indented with ↳ and the note "Task" under it and moves with it. It is edited on the right as in a questionnaire. In the interview view the task stands on the page of its guide question, below the sub-questions: you enter what the person says or lays out, or tap **Show**. The device then shows only the task full screen, without guide, notes and clock, and you hand the tablet over; **Back (press and hold)** brings the interview view back, a short tap is not enough. For a stored interview the result is saved, with the time on the interview clock when it was first entered. Print and Word show the task with its kind. The same kinds are also available in the short questionnaire and the postscript. In the short questionnaire and the postscript of a stored interview the setting “Required” works as in a questionnaire: “Next” goes on only once the required questions are answered, and “End interview” only once those of the short questionnaire are; the postscript can still be written after the end. With “Request” “Next” marks an open question once, a second click continues without an answer.

**Participant screen**: the interviewee solves a task on their own phone or laptop, also in a video interview. In the interview view of a stored interview, “Participant screen” at the top opens the button **Connect participant screen**: it shows a link and its QR code once. The person scans the code or opens the link, in a video interview from the chat; their device shows “Starting soon …” and nothing else of the guide, no notes, no recording, no names. The link opens on one device only, unopened within 30 minutes; the connection ends with “End interview”, with **Disconnect screen** and with **Make a new link**, after 6 hours at the latest. The top bar shows whether the device is connected. At each task, **Show on the participant screen** shows it there, **Take back** takes it back. The result appears at the task at once, as a basis for the conversation, and is stored like one entered, with the time on the interview clock. While the task is out, the task's form is hidden. If the person corrects themselves out loud, **Change result** opens the result for editing; it then counts as “changed by the interviewer”. Interviewers with an interviewer link have the participant screen when the person who created their link has the feature. The short questionnaire can go there too, for instance in interviews over Zoom or Teams: **Show the short questionnaire on the participant screen** shows all its questions at once, the person taps “Send”, their device checks required questions; the answers are in the interview at once, **Change answers** opens them for editing (then “changed by the interviewer”). The postscript stays with you. The template “Information and consent” names the participant screen in a paragraph of its own.

**Poor or no connection**: the interview view loads the whole guide when it opens, so paging, ticks, notes, the short questionnaire and the recording carry on without a network. Whatever has not reached the server yet stays in the device's browser, even if the tab is closed or the battery runs out, and is sent once the connection is back; the line next to the clock and the recording's line then say “offline”. “End interview” without a connection is noted and happens by itself once it is back; keep the page open for that. For a stored interview the page can also be reloaded or opened again without a network: the browser keeps a copy until the interview ends, at most 12 hours. Before the interview the page checks the connection and says next to the recording whether it is good or weak. If the sign-in has expired (interviewer link after 60 minutes without a connection), the line says so; after signing in again in a new tab everything is sent by itself. If audio pieces do not get through at all, **Save audio pieces** saves them as a file; on the interview's page **Upload recording**, **Read in saved audio pieces** reads them in again. This also holds for answers in the short questionnaire, the postscript and tasks. On the first recording the browser is asked to keep the audio pieces for good; if storage on the device runs low, the recording's line says so. The recording page on a second device shows “offline” the same way and sends once the connection is back.

**Offline mode**: for days in the field without any network. Under “Interviews”, allow offline mode for the guide, then on the tablet **Open field kit**. The first time, set a PIN (at least 6 characters); the interviews on the device are encrypted with it and cannot be read without it. With a network, **Prepare for offline**: the server reserves the chosen number of interviews (at most 20) with identifier and deletion code, valid seven days; at most 10 field packs per person or interviewer link and 50 per guide can be open at once. Without a network the field kit still opens; **Start** asks for the consent and opens the interview view, **End interview** shows the deletion code. With a network the field kit syncs by itself (or **Sync everything**) and then deletes its copy. Phone as recorder, participant screen and transcription only work with a network. If a tablet is lost, **Withdraw** its field pack under “Interviews”. The template “Information and consent” names the offline mode once it is allowed.

### Interviews {kind:interview}

The tab for collecting: begin an interview (also "Begin interview" at the top right), continue or edit the interviews conducted with recording, transcript and notes, and create links for interviewers without an account. On the page of a transcript, the foldable "Transcript head" below the text is prefilled from the interview and can be changed; "Word" and "PDF" export the transcript with this head and numbered lines after the transcript template, with time marks if you like. The page header and the interviewee's details from the short questionnaire are chosen in the settings under "Transcript export".

### Results of an interview guide {kind:interview}

Case overview, coverage of the guiding questions, duration per topic block and notes per guiding question; plus the search in the transcripts and the case summaries, and where switched on “Code” (see below). Among the exports are all transcripts as a ZIP, one Word or PDF file per interview with transcript head and line numbers; unchecked ones are marked as drafts.

The results of the **tasks** are in the case overview (CSV) with the columns of their kind, for a ranking one column per term with its rank. In the transcript export each task has a line at its time mark, in italics and without a speaker label, such as "[Task “Importance”: 1 Price, 2 Quality, 3 Brand]"; the transcript head names these insertions, and "Insert the results of the tasks" switches them off. In the REFI-QDA project (.qdpx) each task is a case variable (text) and the same line stands in the transcript; the category system has a code "Aufgabe: …" per task under its guide question, to mark the conversation about it. If the participant screen was used, the case overview has two more columns per task: who gave the result (`<code>_quelle`: Interviewende, Teilnehmerbildschirm or “Teilnehmerbildschirm, vom Interviewenden geändert”) and the seconds from showing to the result (`<code>_dauer_s`); the case overview on the page and the notes note it after the result.

### Coding interviews {kind:interview}

Experimental, switched on per person by an admin. Under “Results” of an interview guide, **Code** opens the analyses of the transcripts. An **analysis** has a name, a research question, a category system of its own and members of its own; all analyses of a guide work with the same transcripts. **New analysis** opens a dialog: start with the guide (all or some topic blocks become main categories, their guide questions subcategories, with the same keys as the category system in the REFI-QDA export), with an empty category system or as a copy of another analysis.

**Settings** per analysis: which interviews the members see (“everyone sees every interview” or “each group its own”; you always see all) and **Main categories fixed**: then only you change the main categories, the members add **candidates** (dashed; “Take on as main category” makes one a main category) and propose renaming or a sharper definition; a lock shows the state. Without the tick, main categories follow the rules of subcategories.

**Category system**: all members add subcategories, with a name and definition (required) and, if they like, a delimitation; every entry carries its author and time. While the name is typed, similar categories show first, with “Use this one”; only then does the button say “Create anyway”. Your own entries you change and remove yourself (the fields save by themselves); for others you propose a change, which whoever may change the entry takes on or declines. **Undo** takes back your own last step as long as nobody changed that category since. The **history** shows every change with name and time.

**Coding**: in the list “Interviews of this analysis”, **Code** opens the transcript. Mark a passage, then choose the category: typing filters, the keys 1 to 9 take the ones used last, Enter the first; **+ New category** creates one and assigns it at once (similar names show first). Whole words are always marked. A click on a coded passage shows its categories with **Remove** (your own passages; you all of them) and **As anchor example** (whoever may change the category); the anchor example then shows in the category system. If a transcript is corrected after coding, each passage finds its wording again; if it is no longer there, the passage shows under **Can no longer be placed** and is marked anew. The coded passages belong to the interview: they go with a deletion request, the period and the guide.

**Overview**: at the top of an analysis's page, buttons open the **Overview** (the category system as a table with passages, interviews, who coded and the check, or as a **Map**, also as SVG), **Passages per category**, **Category × interview** (also as CSV), the whole **History** and the **Coding guide** as a Word file. Two categories are **blurred** when the same passages are in both, the mutual check chooses the one instead of the other, or their names are alike; with **View passages**, **Write a delimitation** and **Merge**. Merging takes effect at once and needs a reason; anyone merges subcategories, only you main categories. Whoever has passages in it sees “change concerns your passages” on top and answers **Fits** or **Take my passages out** (their passages go into a category of their own with the old name); after a rename **Fits** or **Keep the old name**. Whoever merged, and you, take it back in the history; every entry can be commented. **Mutual check**: passages of others without their category, you choose one; the agreement shows per category in the overview.

**Members** with the role “Code” code and add to the category system but do not change the guide: a person (ORCID iD or from your groups) or a whole Askurious group; they open the analysis after signing in at the address shown. Interviewers with an interviewer link get the tick **may code own interviews** per analysis (off until you set it): then their page “My interviews” shows a section “Code” with only their own interviews, also after “Transcript checked”; nothing changes on the interviewer link itself. **Coding links without an account** work like interviewer links: name, expiry date (required, at most 90 days), at most 3 devices, link and QR code shown once, Block, New link, Sign out devices, Extend, Delete. You add the coders and make sure they are bound to confidentiality.

### Export of an interview guide {kind:interview}

The same cards as for a questionnaire: Interviews, Documents, Evidence and archive, Data protection. “Record revision” opens a dialog with occasion, licence and third-party materials.

The guide as Word, the interviews as text and as a REFI-QDA project, the revisions kept and **Delete an interview (data protection)**: with a deletion code or identifier the whole interview is deleted, as "Delete answers" in the export of a questionnaire.

---

## 19. Live poll {kind:live}

![Setting up a live poll: describe the event and let the AI suggest questions, or use a template](img/live-assistant.jpg)

**What it is.**
Feedback in lectures and at events.
Tabs: Settings · Slides · Present · Results · Export.

- **Slides**: rating scales, single choice, word clouds, free text, quiz questions, grouped in blocks; notes for the presenter.
- **Present**: a QR code and a short address for the audience; the **presenter view** for the own laptop (question, result, controls with “‹ Back” and “Next ›” in one row and the other buttons side by side, among them “Show QR code large” (“QR off” switches off the small QR code in the corner of a presentation slide), “Up next” with the following slide or question, notes, questions from the audience below the slide or with “Questions right” beside it, below with a border to drag; the border between the left column and the slide can be dragged, a double click resets it) and the **audience view** for the projector; slides with results as PDF.
- **Pen on presentation slides**: in the presenter view a digital pen writes straight onto a slide from “Slides from PDF”; harder pressure gives broader lines. A finger or the mouse draws with “Draw with finger or mouse”. The button “Pen” opens the tools as a popup: pen in black, red and blue, highlighter, eraser (the eraser end of the pen too), laser dot (briefly on the screen, not saved), undo and redo (Ctrl+Z, Ctrl+Y) and “Clear slide” (click twice). The strokes appear on the screen after about a second, unless “Show on the screen” is off. They belong to the run: a new run starts clean. “Slides with notes (PDF)” under Data and on the page of the slides with results gives every presentation slide with its notes as one page. On the page of the slides with results the notes stand over the presentation slides; “Edit notes” changes them there afterwards for the run chosen, and “Print / save as PDF” takes them along.
- **Runs**: each session is a run (e.g. lecture 14 October); a run can be marked as a trial.

![Slides of a live poll](img/live-questions.jpg)

![The presenter view with 26 answers on a rating question](img/live-speaker-1.jpg)

![A word cloud in the presenter view, with short texts to hide](img/live-speaker-3.jpg)

| The phone of a participant | After sending |
|---|---|
| ![A question on a phone, with the reaction buttons](img/live-phone.jpg) | ![The thank-you page after the end of the poll](img/live-phone-after.jpg) |

**Why.**
Quick feedback from the lecture hall is a common need in teaching; in Askurious the data stay on the same server, under the same data protection as the surveys.
Participants need no account and see only one line about privacy instead of a consent page, because the poll is anonymous and only summarised on the screen.
Short texts can be hidden by the presenter before they appear on the projector.

### Settings of a live poll {kind:live}

Title and language, the researchers involved, participation (reactions, questions from the audience, text after sending), **data protection and retention** and the appearance.
A live poll is never closed, so the period after which all answers are deleted counts from the last participation and starts again with every new one.

### Slides {kind:live}

The slides of the event: questions of every kind, text and image slides, grouped in blocks, with notes for the presenter; "Suggest slides with AI" and "Slides from PDF" help to create them.

### Present {kind:live}

The tab for collecting: QR code and address for the audience, the presenter view and the audience view, and the **runs**; a run can be marked as a trial.
Every phone votes once per question; the server issues and signs its device identifier when the page is opened, and one made up does not count. From one network (one address) at most 300 devices vote on a question, as many as a lecture hall behind a shared address.

### Results of a live poll {kind:live}

The answers of all runs as for a questionnaire, word clouds as on the screen and the questions from the audience; trials do not count as real participations.

### Export of a live poll {kind:live}

The data set of all runs (a column for the run, trials with the source "test") and the live poll as a file with slides and settings.
