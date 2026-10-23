"""What AInstein knows about study designs and about Askurious, for its system prompt (app/ainstein.py, MENTOR).

DESIGNS: typical designs as cards with the same structure (fits when, structure, in Askurious, watch out, does not fit
when), so the model picks the design from the research question and does not lean toward one method because the
prompt praises it (Philipp, 2026-10-06: AInstein should not nudge toward Conjoint). Written in English for the model;
AInstein answers in the language of the researcher.

capabilities(): the element types of Askurious, generated from the registry (app/items) and the fact sheet of each type
(app/element_types.py), so a new type reaches AInstein without touching this file; experimental types only when the researcher has them switched on (app/experimental.py).

Both texts stay the same from turn to turn and stand early in the instructions, so the provider can cache them."""
from app import experimental, items

# The cards of DESIGNS by key, named (German, English) for the overview page "Elementtypen" and the fact sheets of the
# element types (items.base.FactSheet.designs); the English name begins the title of its card
# (tests/items/test_element_types.py)
DESIGN_NAMES = {
    "cross": ("Querschnittsbefragung", "Cross-sectional survey"),
    "perception": ("Wahrnehmungsbefragung", "Public perception survey"),
    "framing": ("Framing-Experiment", "Framing / message experiment"),
    "ab": ("A/B-Test", "A/B test"),
    "within": ("Messwiederholung", "Repeated measures"),
    "vignettes": ("Vignettenstudie", "Vignette study"),
    "choice": ("Wahlexperiment", "Choice experiment"),
    "panel": ("Längsschnitt", "Longitudinal study"),
    "delphi": ("Real-Time-Delphi", "Real-time Delphi"),
    "interview": ("Qualitatives Interview", "Qualitative interview"),
}

DESIGNS = """\
- Cross-sectional survey with scales (the most common case)
  Fits when: how widespread something is, how people see or evaluate a topic, or how constructs are associated
  (e.g. "Does trust in AI go along with its use?"), including mediators and moderators.
  Structure: one measurement per person; validated scales, single questions, demographics.
  In Askurious: Likert and rating scales, constructs from the toolbox, single and multiple choice, open questions.
  Watch out: shows associations, not causes; common-method bias; length of the questionnaire.
  Does not fit when: the question is whether X causes Y (then an experiment).
- Public perception survey
  Fits when: describing the attitudes, knowledge, hopes and fears of the general public or a group toward a topic
  (technology, risk, policy), often with comparisons between groups.
  Structure: cross-sectional; often quota sample (age, gender, region) or panel; mostly descriptive and group
  differences.
  In Askurious: scales, single questions, free associations with valence, risk-benefit positioning on two axes
  (position2d), quotas, region from postal code or federal state, entry and exit via panel providers.
  Watch out: who is reached (convenience samples do not generalise); wording effects; acquiescence.
  Does not fit when: the effect of a message or feature is the point (then a framing experiment or A/B test).
- Framing / message experiment (between subjects)
  Fits when: whether the way something is presented (wording, frame, source, image) changes attitudes, intentions or
  judgements.
  Structure: random assignment of each person to one condition; the same outcome measures after the stimulus;
  manipulation check; often a control condition.
  In Askurious: random assignment of conditions in the flow, a block per condition, AI or own images as stimuli,
  manipulation check questions.
  Watch out: effects are often small, so many participants per condition (often 100 or more); keep conditions
  identical except for the frame.
  Does not fit when: few participants are available (consider within subjects) or several attributes vary at once
  (then vignettes or a choice experiment).
- A/B test of variants
  Fits when: two or a few concrete variants (a design, a text, a product, an interface) are compared on one clear
  outcome (preference, rating, choice, comprehension); practical rather than theoretical.
  Structure: random assignment to one variant; one main outcome decided in advance.
  In Askurious: random assignment in the flow, images or texts per variant, rating, single choice.
  Watch out: decide the main outcome and sample size in advance; differences between variants must be clear.
  Does not fit when: why something works is the question (then a framing experiment with a theory) or many features
  vary (then a choice experiment).
- Repeated measures / within-subjects experiment
  Fits when: the same people judge several conditions or stimuli, or are measured before and after an intervention;
  also the answer when the sample is small.
  Structure: every person sees all conditions in random order (or before-after around an intervention in the same
  session; several waves on different days are a longitudinal study).
  In Askurious: loops over stimuli, random order, measurements before and after an intervention block, reaction-time
  tasks.
  Watch out: carry-over and order effects (randomise the order); demand effects when people see all conditions.
  Does not fit when: seeing one condition changes how people see the next (then between subjects).
- Vignette study (factorial survey)
  Fits when: how several factors of a described situation together shape a judgement (e.g. "When is a decision by
  an AI seen as fair?").
  Structure: short scenarios whose factors vary systematically; each person rates a set of vignettes on scales.
  In Askurious: vignette loops from factors and levels with an experimental design, decks per person.
  Watch out: plausible combinations; not too many vignettes per person.
  Does not fit when: choices between options or trade-offs are the point (then a choice experiment).
- Choice experiment (Conjoint, ACBC, MaxDiff)
  Fits when: which attributes and levels people prefer and how they trade them off (Conjoint, ACBC), or which of many
  features matter most (MaxDiff); willingness to pay.
  Structure: repeated choices between profiles; estimates part-worth utilities and relative importances (Hierarchical
  Bayes), not a test per attribute.
  In Askurious: Conjoint (CBC) with tables or AI-generated images of each profile, adaptive Conjoint, MaxDiff,
  simulator for market shares, Van Westendorp for price ranges.
  Watch out: 3 to 6 attributes with 2 to 5 levels; realistic levels; enough tasks; image profiles only for
  attributes that can be seen.
  Does not fit when: attitudes or associations are the question, or there are no clear attributes.
- Longitudinal study (several waves)
  Fits when: change over weeks or months, or which comes first.
  Structure: the same people at two or more time points on different days.
  In Askurious: one survey per wave, linked through an ID passed in the link (e.g. from a panel provider) or a
  self-generated code the participants enter; there is no built-in management of waves.
  Watch out: drop-out between waves; keep measures identical.
- Real-time Delphi (expert panel)
  Fits when: experts are to estimate or judge the future, a risk or a priority (e.g. "When will robots care for
  older people?") and the aim is a considered group judgement, consensus or dissent.
  Structure: experts answer each projection (probability, impact, desirability, when), then see how the other experts
  have answered so far and may revise their answer and give reasons; no fixed rounds: feedback grows with every
  participant.
  In Askurious: the projection as rating, slider or scale; then a feedback element (sample comparison or crowd: the
  answers of all before, the own one highlighted) and the same question again to revise, plus an open question for
  reasons; consensus from the spread of the revised answers.
  Watch out: who counts as an expert and how many (often 15 to 50); the first participants see little feedback
  (feedback starts at 5 answers); clear projections, one statement each.
  Does not fit when: the opinion of the general public is the question (then a survey).
- Qualitative interview
  Fits when: little is known and the aim is to understand experiences, reasons and meanings in depth; often before
  a survey to find the right constructs.
  In Askurious: interview guides with narrative prompts, follow-up questions, recording, transcription and coding.
  Does not fit when: frequencies or effects in a population are the question."""


def capabilities(user=None):
    """The element types as lines "- German name: what it is (fits: designs)" (one language, no keys; Philipp 2026-10-06),
    grouped as in the palette; experimental ones only when they are switched on for `user` (by default the one signed
    in). The whole fact sheet of the types the conversation names comes with the context (ainstein._context)."""
    from app import element_types

    groups = {"items": [], "feedback": []}
    for t in items.all_types():
        if getattr(t, "interview_only", False):
            continue
        if getattr(t, "experimental", None) and not experimental.enabled(t.experimental, user):
            continue
        groups.setdefault(getattr(t, "category", "items"), []).append(element_types.short_line(t))
    return ("Question and task types:\n" + "\n".join(groups["items"]) +
            "\nFeedback to participants after their answers:\n" + "\n".join(groups["feedback"]))
