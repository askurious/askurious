"""AI feedback on the image prompts of a choice task (conjoint, ACBC): the base prompt and the image hint of every
level of the attributes shown in the images, checked by the text model for clarity as parts of an image prompt.

The model sees what the image model sees and nothing else: the image style, the base prompt, the image
hints (attribute codes as names) and a few prompts put together from them. Labels, levels and definitions that
participants read are not sent (app/items/attributes.py keeps the two apart).

The answer is a list of suggestions: a new base prompt, a new hint per level, each with a reason, and general
remarks. Nothing changes until the researcher ticks suggestions and takes them over (editor.item_images_review_apply);
images already made keep their prompt."""
import json

from app import ai

INSTRUCTIONS = """You review the prompts for AI-generated images that serve as stimuli in a choice experiment (conjoint
analysis) of a social science survey. Every image shows one option. Its prompt is put together from: the image style,
a base prompt (the scene every image shares), and for each attribute shown in the images the image hint of the
level the option has. Participants compare the images; the analysis attributes their choices to the attributes.

Check every part for clarity as part of an image prompt, and that the images will differ only in the attributes:
- The base prompt fixes everything that must stay the same in every image: subject, setting where it is not an
  attribute, people (age, gender, clothing, pose) where people appear, camera angle and framing, lighting.
  Anything left open varies between images and confounds the attributes.
- Each hint is a concrete visual description (what can be seen), not an instruction ("Design should feature ..."),
  not a meaning or a feeling, in plain words, one or two short sentences.
- The levels of one attribute are written in parallel: same structure, same length, the same unit for sizes; they
  differ only in the level itself.
- A hint describes its own attribute only and does not contradict the base prompt or the other attributes.
- No text, letters, numbers, logos or brand names in the image.
- Fix typos, stray words and broken sentences.

Suggest a change only where it makes the prompt clearer; leave good parts alone. Keep the meaning of every level.
Answer with ONE JSON object and nothing else:
{"remarks": ["general remark", ...],
 "scene": {"suggestion": "new base prompt", "why": "short reason"} or null,
 "levels": [{"level": "<level id>", "suggestion": "new hint", "why": "short reason"}, ...]}
Reasons and remarks in German; write each prompt and hint in the language it is written in now (German or
English), do not translate."""


def facts(item):
    """What the text model gets: style, base prompt, attributes with their level hints, example prompts."""
    from app import image_styles

    kind = item.kind
    attrs = [{"attribute": a["code"],
              "levels": [{"level": l["id"], "hint": (l.get("hint") or "").strip()} for l in a.get("levels", [])]}
             for a in kind.prompt_attributes(item)]
    examples = [kind.prompt_for_key(item, key) for key in kind.image_page_keys(item)[:3]]
    # images per level have no base prompt (each level's hint is the whole subject)
    scene = "" if kind.level_images_on(item) else (item.config.get("scene") or "").strip()
    return {"style": image_styles.style_prompt(item.survey), "base_prompt": scene,
            "attributes": attrs, "example_prompts": examples}


def review(item):
    """{"remarks": [...], "scene": {"old", "suggestion", "why"} | None, "levels": [{"attribute", "level", "label",
    "old", "suggestion", "why"}]}; only suggestions that change something and name a level of this item."""
    data = ai.json_answer(ai.generate_text(INSTRUCTIONS, json.dumps(facts(item), ensure_ascii=False, indent=1),
                                    purpose="image_prompt_review", item_type=item.type))
    labels = item.t(item.survey.default_language).get("levels", {})
    levels = {l["id"]: (a, l) for a in item.kind.prompt_attributes(item) for l in a.get("levels", [])}
    out = {"remarks": [str(r)[:500] for r in ai.as_list(data.get("remarks")) if str(r).strip()][:10], "scene": None, "levels": []}
    scene, old = data.get("scene"), (item.config.get("scene") or "").strip()
    if isinstance(scene, dict) and not item.kind.level_images_on(item) and str(scene.get("suggestion") or "").strip() not in ("", old):
        out["scene"] = {"old": old, "suggestion": str(scene["suggestion"]).strip()[:2000], "why": str(scene.get("why") or "")[:500]}
    for s in ai.as_list(data.get("levels")):
        if not isinstance(s, dict) or not isinstance(s.get("level"), str) or s["level"] not in levels:
            continue
        a, level = levels[s["level"]]
        new = " ".join(str(s.get("suggestion") or "").split())[:300]
        if new and new != (level.get("hint") or "").strip():
            out["levels"].append({"attribute": a["code"], "level": level["id"], "label": labels.get(level["id"], ""),
                                  "old": (level.get("hint") or "").strip(), "suggestion": new, "why": str(s.get("why") or "")[:500]})
    return out


def apply(item, scene=None, hints=None):
    """Take over the ticked suggestions: a new base prompt and {level id: new hint}."""
    import copy

    config = copy.deepcopy(item.config)
    if scene is not None:
        config["scene"] = scene.strip()[:2000]
    for a in config.get("attributes", []):
        for level in a.get("levels", []):
            if level["id"] in (hints or {}):
                level["hint"] = " ".join(hints[level["id"]].split())[:300]
    item.config = config
