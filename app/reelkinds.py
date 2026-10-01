"""Форматы рилсов (5.5): что это, на каком движке собирается и что Claude просить.

Движки:
  story      — одна картинка, камера ходит по деталям под голос (Remotion: Story). «Детали картины»,
               «Одна фотография», «Масштаб» (от человека к зданию), «Разбор здания» (линии поверх фото).
  pair       — две картинки и переход между ними (Remotion: Pair). «Чертёж → здание», «Тогда / сейчас»,
               «Картина и место», «Кадр ← картина», «Что под слоем», «Какая из двух».
  collection — подборка работ разных авторов (Remotion: Collection).

Рубрика — строка сверху кадра. Чередование форматов в автоплане — REEL_ROTATION (см. reels.next_kind).

Язык (5.6): рилс бывает на английском (en) или на русском (ru). Промпты написаны по-английски; для русского
к ним добавляется RU_RULES (как писать по-русски) и RU_NAMES (русские названия для экрана: название, автор,
музей). Рубрика и подписи A / B — rubric_ru и roles_ru."""
import os

KINDS: dict[str, dict] = {
    "details": {"ru": "детали картины", "acc": "детали картины", "icon": "🔍", "engine": "story",
                "rubric": os.getenv("REEL_RUBRIC", "Paintings, closely"), "rubric_ru": "Картины вблизи",
                "obj": "картина", "subject": "painting"},
    "collection": {"ru": "подборка", "acc": "подборку", "icon": "🖼", "engine": "collection", "rubric": "",
                   "obj": "тема", "subject": "collection"},
    "photo": {"ru": "одна фотография", "acc": "«Одну фотографию»", "icon": "📷", "engine": "story",
              "rubric": "Photographs, closely", "rubric_ru": "Фотографии вблизи", "obj": "фотография",
              "subject": "photograph"},
    "scale": {"ru": "масштаб", "acc": "«Масштаб»", "icon": "🧍", "engine": "story",
              "rubric": "Architecture, to scale", "rubric_ru": "Архитектура в масштабе", "obj": "здание",
              "subject": "building"},
    "read": {"ru": "разбор здания", "acc": "разбор здания", "icon": "📐", "engine": "story",
             "rubric": "Buildings, read closely", "rubric_ru": "Здание по линиям", "obj": "здание",
             "subject": "building"},
    "plan": {"ru": "чертёж → здание", "acc": "«Чертёж → здание»", "icon": "✏️", "engine": "pair", "layout": "dissolve",
             "rubric": "Drawn, then built", "rubric_ru": "Нарисовано, потом построено", "obj": "пара",
             "roles": ("Drawn", "Built"), "roles_ru": ("Чертёж", "Здание")},
    "thennow": {"ru": "тогда / сейчас", "acc": "«Тогда / сейчас»", "icon": "🕰", "engine": "pair", "layout": "wipe",
                "rubric": "Then and now", "rubric_ru": "Тогда и сейчас", "obj": "место",
                "roles": ("Then", "Now"), "roles_ru": ("Тогда", "Сейчас")},
    "place": {"ru": "картина и место", "acc": "«Картину и место»", "icon": "📍", "engine": "pair", "layout": "wipe",
              "rubric": "Painted, and real", "rubric_ru": "На картине и наяву", "obj": "картина",
              "roles": ("Painted", "Real"), "roles_ru": ("Картина", "Наяву")},
    "film": {"ru": "кадр ← картина", "acc": "«Кадр ← картина»", "icon": "🎞", "engine": "pair", "layout": "split",
             "rubric": "Painting into film", "rubric_ru": "Из картины в кино", "obj": "пара",
             "roles": ("Painting", "Film"), "roles_ru": ("Картина", "Фильм")},
    "layer": {"ru": "что под слоем", "acc": "«Что под слоем»", "icon": "🩻", "engine": "pair", "layout": "wipe",
              "rubric": "Under the surface", "rubric_ru": "Под красочным слоем", "obj": "картина",
              "roles": ("Visible", "Beneath"), "roles_ru": ("Видно", "Под слоем")},
    "which": {"ru": "какая из двух", "acc": "«Какую из двух»", "icon": "⚖️", "engine": "pair", "layout": "split",
              "rubric": "Which one?", "rubric_ru": "Какая из двух?", "obj": "пара",
              "roles": ("A", "B"), "roles_ru": ("А", "Б")},
}
ORDER = ["details", "photo", "scale", "read", "collection", "plan", "thennow", "place", "film", "layer", "which"]
# автоплан: «детали» чаще остальных, пока статистика не подскажет другое
ROTATION = [k.strip() for k in os.getenv(
    "REEL_ROTATION", "details,scale,collection,photo,plan,details,thennow,read,place,details,film,which,layer").split(",")
    if k.strip() in KINDS]


def ru(kind: str) -> str:
    return KINDS.get(kind, {}).get("ru", kind)


def acc(kind: str) -> str:
    return KINDS.get(kind, {}).get("acc", kind)


def engine(kind: str) -> str:
    return KINDS.get(kind, {}).get("engine", "story")


# ======================= язык =======================

LANGS = {"en": "EN", "ru": "RU"}
LANG_RU = {"en": "на английском", "ru": "на русском"}


def lang_of(d: dict | None) -> str:
    """Язык рилса: d["lang"]; старые рилсы без него — английские."""
    lang = (d or {}).get("lang")
    return lang if lang in LANGS else "en"


def rubric(kind: str, lang: str = "en") -> str:
    k = KINDS.get(kind, {})
    return (k.get("rubric_ru") if lang == "ru" else None) or k.get("rubric", "")


def roles(kind: str, lang: str = "en") -> tuple:
    k = KINDS.get(kind, {})
    return (k.get("roles_ru") if lang == "ru" else None) or k.get("roles", ("A", "B"))


# подписи строк этикетки в конце story-рилса
META_NAMES = {"details": ("Medium", "Size", "Collection"), "photo": ("Process", "Size", "Collection"),
              "scale": ("Material", "Scale", "Place"), "read": ("Material", "Size", "Place")}
META_NAMES_RU = {"details": ("Техника", "Размер", "Собрание"), "photo": ("Техника", "Размер", "Собрание"),
                 "scale": ("Материал", "Масштаб", "Место"), "read": ("Материал", "Размеры", "Место")}


def meta_names(kind: str, lang: str = "en") -> tuple | None:
    return (META_NAMES_RU if lang == "ru" else META_NAMES).get(kind)


RU_RULES = """LANGUAGE — RUSSIAN. The examples above are in English only to show the structure: do not translate them. Write every line that is spoken or shown on screen — hooks, context, reveals, climax, final, labels, figure_label, intro, the lines of the works, the on-screen title — and the caption in Russian, written in Russian from the start, not a translation from English. Keep in English: delivery, context_delivery, final_delivery, intro_delivery, voice_direction, target, type, and anything used to search for images. Where these Russian rules differ from the English ones above, follow the Russian rules.

The voice: an older friend who knows art well and shows you something he loves, as if letting you in on a secret. Warm, curious, a little ironic. Never a lecturer, never an announcer. Example of the tone (structure only, do not reuse): «Хотите, покажу, на что тут почти никто не смотрит? На этом балу не смеётся только шут. Весь двор в соседнем зале, музыка, танцы. А он сидит один, почти в темноте. Видите письмо на столе? В нём новость: Смоленск взят Москвой. Война проиграна. А за дверью всё ещё танцуют.»

How Russian should sound here:
- Invite and steer the eye, addressing the viewer with «вы»: «Хотите, покажу…?», «Видите…?», «Теперь взгляните…», «Представляете:». One or two such questions or invitations per story, not in every line. A hook may be an invitation like this.
- Short sentences, uneven living rhythm, fragments are fine («Музыка, танцы.»). Present tense for what we see.
- Each reveal is a small discovery: point to the detail, name it, then say what it means. Lines are joined by cause and contrast: «а», «но», «поэтому», «потому что», «только». Never «а также», «кроме того», «теперь посмотрите на» as filler.
- One ellipsis is allowed for suspense right before a key word («Самый грустный человек на этом балу... шут.»). No exclamation marks.
- No officialese: no «является», «данный», «представляет собой», «осуществлять», «в рамках», no chains of genitives, no passive voice, no verbal nouns where a verb works.
- No construction «не X, а Y» and no «это не просто X». No aphoristic closing line, no moral, no slogan: the final line is a fact about the work or the artist.
- Never: шедевр, невероятный, потрясающий, культовый, легендарный, завораживающий, удивительный, гениальный, магия, «досмотрите до конца», «подписывайтесь», «вы не поверите». No emoji, no parentheses, no abbreviations (г., в., т. е., ок.), at most one dash in the whole story.
- The narrator reads exactly what is written. Years in digits (1563); ages, counts and sizes in words the way people say them («двадцать четыре», «восемьдесят четыре колонны»). Write ё where it belongs (всё, ещё, её).
- Names in the standard Russian form (Ян Матейко, Доротея Ланг, Андреа Палладио, Фриц Ланг, музей Прадо); titles of works as in Russian Wikipedia or Russian museum practice, otherwise a plain Russian translation. Film titles — as released in Russian.
- 55–70 words in total; keep each part shorter than its limit in words.
- *Emphasis* with asterisks and the ^ mark work exactly as described above: the asterisk word is the one the narrator leans on; ^ goes on a Russian word.
- Delivery hints (in English) become the narrator's intonation. Build them from these words: "curious" for invitations and discoveries, "wry" for the ironic beat, "whisper" for the single most hidden detail (at most once per story), "soft" for the turn, "thoughtful" for the final line. Keep each hint short.
- hashtags — 5–8 lowercase words without #, mostly Russian, multi-word tags written together (историяискусства); one or two English ones are fine."""

RU_NAMES = """The reel is in Russian, so the screen shows Russian names. Keep "title", "author", "museum", "medium" and "size" in English (they are used to find and check the image) and add "ru": {"title": "...", "author": "...", "museum": "...", "medium": "...", "size": "..."} — the established Russian title of the work (Russian Wikipedia or Russian museum practice, otherwise a plain translation), the author's name in the standard Russian form, the museum or place in Russian ("Национальный музей, Варшава"), technique and size in Russian ("Холст, масло", "88 × 120 см"); leave a field empty if unknown."""

RU_NAMES_PAIR = """The reel is in Russian. Keep every field as described, in English, and add to the top level "title_ru" — the short reel name in Russian — and inside "a" and "b" a "ru": {"title": "...", "author": "...", "museum": "..."} with the established Russian title of the work, the author's name in the standard Russian form and the museum or place in Russian."""

RU_NAMES_WORKS = """The reel is in Russian. The on-screen "title" of the collection, "theme_ru", "intro", every "line" and the caption are in Russian. Each work keeps "title", "author" and "commons" in English or the original language (they are used to find the image) and adds "title_ru" — the established Russian title (Russian Wikipedia or Russian museum practice, otherwise a plain translation) — and "author_ru" — the author's name in the standard Russian form."""


def localize(system: str, lang: str, names: str = "") -> str:
    """Промпт для языка рилса: для русского — правила русского текста и русские названия для экрана."""
    if lang != "ru":
        return system
    return system + "\n\n" + RU_RULES + ("\n\n" + names if names else "")


def localize_pick(system: str, lang: str, names: str = RU_NAMES) -> str:
    """Промпт выбора объекта: тексты не пишутся, нужны только русские названия."""
    return system + ("\n\n" + names if lang == "ru" else "")


# ======================= общие правила текста =======================

TASTE = """AHMAG taste: built architecture (modernism and post-war classics, private houses, sacred buildings, ruins, memorials, brick, concrete, stone, wood, light, landscape); art without kitsch (quiet metaphysics and surrealism, land art, prints, manuscripts, old visual culture, warm humour); documentary, street and archival photography, ethnography; historical series. Never: renders, parametric architecture, commercial towers, developer projects."""

MUSIC = """music — 3 tracks that fit the mood and are likely in Instagram's music library: real, well-known recordings (film scores, classical, ambient, jazz, indie). Artist and track exactly as published; mood — 2–4 words in Russian."""

NEVER = """Never: insane, mind-blowing, crazy, iconic, masterpiece, stunning, breathtaking, haunting, heartbreaking, chilling, fascinating, intricate, secret, "dive in", "wait for the end", "follow for more", "let that sink in", "not X but Y" constructions, exclamation marks, emoji, parentheses, abbreviations, lists."""

ENGINE_RULES = """THE JOB: the viewer must not scroll away. They decide in the first second, and they stay only while a question is open. So this is a story with tension, told by one person to a friend standing next to them — never a list of facts.

The engine of retention:
- The hook opens ONE main question. The story answers it only at the climax. Until then every line gives part of the answer and opens a smaller new question.
- Lines are joined by cause and contrast — "but", "so", "which is why", "and that's the problem" — never by "and also", "now look at", "there's more". If a line could be swapped with the next one without breaking anything, the story is a list: rewrite it.
- Stakes in human terms: who made it, who lost what, who knew and who didn't. No jargon unless it IS the story.
- Specific beats general: a name, a number, an object the viewer can see.
- The final line turns the hook around: after it, the first frame reads differently, so the loop back to the start feels natural.

How it sounds: present tense for what we see; talk to the viewer and steer their eye ("Look at the door."); short sentences, varied rhythm, some fragments; plain spoken English."""

HOOK_RULES = """hooks — 4 alternative opening lines, each of a different type (contradiction, hidden, stakes, challenge, question). The first sentence is at most 8 words and lands in under two seconds; the whole hook at most 14 words. No names, dates or titles in the hook. It talks about what is on screen in the first frame. True, specific, visual. No generic hooks ("This building hides a dark secret", "You won't believe…"). Check each hook before you give it: understood in one second without sound? about what is on screen? opens a question? concrete? Give only hooks that pass. hook_pick — the index of the strongest."""

TAIL_RULES = """Every word of emphasis — the one word per sentence the narrator leans on — is wrapped in asterisks: "Nobody *noticed*." At most one per sentence; the screen sets it in italics.

Use only the facts given; if the story needs a fact you don't have, change the angle instead of inventing one.
""" + NEVER + """

delivery — every hook, reveal, the climax, and the context and final lines (context_delivery, final_delivery) get a short direction for the narrator, in English, 6–15 words: tone, emotion, pace, where to pause. Follow the arc: hook — no warm-up, first word straight away, quiet intrigue, a little quicker; context — lower, the stakes sinking in; reveals — curiosity that builds; climax — slower, softer, heavier, a real pause between sentences; final — calm and weighty. Alive and specific, like notes from a director ("lean on 'only'", "a wry smile here", "let it hang"), never theatrical.

voice_direction — one or two sentences for the narrator about this particular story: its mood, where it turns.

hashtags — 5–8 lowercase words without #."""


# ======================= выбор объекта: story =======================

PICK = {
    "photo": f"""You pick a photograph for an AHMAG Instagram reel that walks through its details: the camera moves across one photograph, one short spoken line per detail, and together the lines tell the story behind the picture.

Pick a historical or documentary photograph that is in the public domain (US government work such as FSA/OWI, Library of Congress collections, photographers who died before about 1955, or published before 1930) and has a large image on Wikimedia Commons. It must have several visible details that carry the story (hands, a sign, a face in the background, an object) and a documented story with tension that a stranger would care about in two seconds — who these people are, what happened just before or after, what the photographer did or didn't know. Check the facts with web search. Prefer photographs that are not the most overexposed. Not from the avoid list.

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "common English title of the photograph", "author": "photographer", "year": "...", "museum": "collection, e.g. Library of Congress", "medium": "e.g. Gelatin silver print, if known", "size": "", "commons": "query for Wikimedia Commons search: title and photographer", "facts": ["6–10 specific verified facts in English about the people, the place, what happened, the photographer"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}""",

    "scale": f"""You pick a building or structure for an AHMAG Instagram reel about SCALE: the reel opens on a tiny person (or a door, a car) in a photo, and the camera slowly pulls back until the whole structure fills the frame. The shock of the size is the story — and why anyone built it that big.

Pick built architecture or engineering in AHMAG's taste: cathedrals, hypostyle halls, stepwells, dams, bridges, brutalist megastructures, ruins, memorials, monasteries in cliffs, bunkers, silos. Never commercial towers or developer projects. There must be a large photo on Wikimedia Commons (free licence) where the whole structure is visible AND at least one person (or a car, a door) is visible and small. Facts must include real dimensions (height, length, weight, years to build, how many people) and the human reason for the size. Check facts with web search. Not from the avoid list.

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "name of the building", "author": "architect or builders", "year": "year or years built", "museum": "city, country", "medium": "main material, e.g. Reinforced concrete", "size": "the key dimension, e.g. 152 m tall", "commons": "query for Wikimedia Commons: the building + people or visitors", "facts": ["6–10 specific verified facts with numbers"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}""",

    "read": f"""You pick a building for an AHMAG Instagram reel that READS a building like an architect: over one clear photo of its main facade, thin lines draw the architect's moves one by one — the axis, the grid, the levels, a proportion — and a narrator explains what each move does to the person who walks in.

Pick built architecture in AHMAG's taste whose design is legible from one frontal photo: classical and Renaissance villas and temples, modernist and post-war buildings, rationalism, brick and concrete. There must be a large, sharp, frontal, not strongly distorted photo of the whole facade on Wikimedia Commons (free licence). The design logic must be documented (proportions, dimensions, the architect's own words, what the building was for). Check facts with web search. Not from the avoid list.

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "name of the building", "author": "architect", "year": "...", "museum": "city, country", "medium": "main material", "size": "key dimensions if known", "commons": "query for Wikimedia Commons: building name + facade", "facts": ["6–10 specific verified facts: proportions, dimensions, intentions, history"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}""",
}

# что должно быть на картинке — для проверки кандидатов с Commons
NEED = {
    "details": "",
    "photo": "the photograph itself, whole, not a museum wall or a book page; a good clean scan",
    "scale": "the whole structure is visible AND at least one person (or a car, a door) is visible and small; sharp, large",
    "read": "a sharp, frontal, straight (little perspective distortion) photo of the whole main facade, no scaffolding",
}


# ======================= рассказ: story =======================

STORY_KIND = {
    "photo": {
        "what": "the photograph in the image",
        "example": """Example of the voice (Dorothea Lange, "Migrant Mother"; do not reuse its lines):
  hook: "Two of her children are hiding their *faces*."
  context: "She's thirty-two. The photographer stays ten minutes."
  reveals: "Look at her ^hand. It's on her chin, and she's not looking at *us*." / "Because the ^pea crop froze. There is no work, and no food." / "But the ^tent behind her is all they have. They've just sold their car's tyres."
  climax: "She never got a *cent* for it."
  final: "The picture made her the face of the Depression. She spent forty years trying to take it *back*.\"""",
        "structure": """Structure — 25 to 35 seconds, 70–90 words in total:
1. hooks — as described.
2. context — up to 14 words, shown over the whole photograph: it RAISES the stakes, it does not explain. Never "X took this in Y" here.
3. reveals — 3 details (4 only if needed), each up to 18 words, one step of the story each; label — 1–3 words naming the detail; mark with ^ the word at which the camera arrives at the detail.
4. climax — up to 12 words: the answer to the hook's question for the people in the picture. One brief human note. box — the detail to hold on, or null.
5. final — up to 20 words: what happened next. Lands with weight, echoes the hook.""",
        "schema": "",
    },
    "scale": {
        "what": "the photograph of a building in the image",
        "example": """Example of the voice (Chand Baori stepwell; do not reuse its lines):
  hook: "That dot on the stairs is a *person*."
  context: "And the stairs keep going. Down, not up."
  reveals: "Thirteen ^floors, dug into the ground. Three and a half thousand *steps*." / "Because this is a ^desert. The rain comes three months a year." / "So they built the whole ^well as a staircase to wherever the water *was*."
  climax: "It's not a building. It's a hole the size of a *cathedral*."
  final: "It's been there twelve hundred years. The water level still decides which step you *stop* on.\"""",
        "structure": """Structure — the camera starts extremely close on a small person and pulls back in steps until the whole structure is on screen. 25 to 35 seconds, 70–90 words:
1. hooks — as described; the hook's box is tight around the small person (or car, door) — the thing that gives the scale. Every hook talks about that small thing.
2. figure_label — 2–4 words shown next to the person when we pull back: their real size ("1.7 m", "a car, 4 m").
3. context — up to 14 words; context_box — a wider view around the person (about 3–4 times the hook box, still not the whole building). It raises the stakes.
4. reveals — 2 or 3 steps, each up to 18 words, each box wider than the previous one (the camera keeps pulling back), each with a real number or fact about the size and WHY it is so big. label — 1–3 words. Mark with ^ the word at which the camera arrives.
5. climax — up to 12 words, box null: the whole structure on screen at last. The number that lands.
6. final — up to 20 words: the human reason or consequence. Echoes the hook.""",
        "schema": ', "figure_label": "1.7 m", "context_box": [0.1, 0.2, 0.3, 0.5]',
    },
    "read": {
        "what": "the photograph of a building facade in the image",
        "example": """Example of the voice (Giuseppe Terragni, Casa del Fascio, Como; do not reuse its lines):
  hook: "This facade is *exactly* half a cube."
  context: "Every window here obeys that one number."
  reveals: "The ^grid on the left is open. You can see straight *through* the building." / "But the right side is a solid ^wall. One gesture of stone against all that *glass*." / "And the ^top line never breaks. The whole block reads as one *slab*."
  climax: "Glass for the public. Stone for the *state*."
  final: "Terragni called it a house of glass. It became the police headquarters *after* the war.\"""",
        "structure": """Structure — over one facade photo; for each reveal a thin line diagram is drawn over the photo. 25 to 35 seconds, 70–90 words:
1. hooks — as described; the hook's box is the detail or the part of the facade the hook is about.
2. context — up to 14 words over the whole facade; raises the stakes.
3. reveals — 3 architect's moves (4 only if needed), each up to 18 words: what the move is AND what it does to the person who uses the building. label — 1–3 words ("The axis", "The grid"). Mark with ^ the word at which the drawing appears. box — the part of the facade the move is about. mark — which line diagram is drawn on that box:
   "axis" — a vertical line through the middle of the box (symmetry, the entrance axis; the box should be centred on the axis);
   "level" — a horizontal line across the box (a cornice, a floor, the ground line);
   "grid" — the box divided into cols × rows (the rhythm of windows or bays; give "cols" and "rows" as the real counts in the box);
   "frame" — the outline of the box (a block, an opening, a mass);
   "diagonals" — the outline and both diagonals (a proportion: a square, a golden rectangle).
   The box must be tight and exact, because the lines are drawn on it.
4. climax — up to 12 words: what all these moves add up to. box — null (the whole facade), all the lines are shown together.
5. final — up to 20 words: what happened to the building, or what the architect said. Echoes the hook.""",
        "schema": "",
        "reveal_schema": ', "mark": "axis|level|grid|frame|diagonals", "cols": 5, "rows": 4',
    },
}


def story_system(kind: str) -> str:
    """Промпт рассказа для story-форматов, кроме «деталей» (у них — выверенный STORY_SYSTEM в reels.py)."""
    k = STORY_KIND[kind]
    reveal_extra = k.get("reveal_schema", "")
    return f"""You write the narration for an AHMAG Instagram reel about {k['what']}. A narrator reads it aloud, the words appear on screen as they are spoken, and the camera moves over the image. Coordinates are fractions of the image width and height from its top-left corner (0 to 1).

{ENGINE_RULES}

{k['example']}

{k['structure']}

{HOOK_RULES}

Boxes — [x0, y0, x1, y1], tight around the thing itself. target — 2–6 words naming exactly what is inside the box; a second pass uses it to refine the box, so be precise. Only things clearly visible in THIS image.

{TAIL_RULES}

caption — the Instagram caption WITHOUT the hook (the hook is put above it automatically): first line "Title (year), Author"; then 2–3 short paragraphs with the story and one or two facts that did not fit the video; last line — place or collection.

Return ONLY JSON:
{{"hooks": [{{"type": "contradiction|hidden|stakes|challenge|question", "text": "...", "box": [0.1, 0.2, 0.3, 0.5], "target": "...", "delivery": "..."}}], "hook_pick": 0, "context": "...", "context_delivery": "..."{k['schema']}, "reveals": [{{"box": [0.1, 0.2, 0.3, 0.5], "target": "...", "label": "...", "text": "...", "delivery": "..."{reveal_extra}}}], "climax": {{"text": "...", "box": null, "target": "...", "delivery": "..."}}, "final": "...", "final_delivery": "...", "voice_direction": "...", "caption": "...", "hashtags": ["..."]}}"""


# ======================= пары =======================

PAIR_PICK = {
    "plan": """a building together with a historical drawing of it — the architect's own plate, elevation or perspective, or a contemporary engraving (public domain) — and a photo of the building as it stands today. The tension: it was built exactly as drawn centuries later, or it was built differently, or the drawing was an idealised lie. a = the drawing (role "drawing"), b = the built building (role "built"). Great sources: Palladio's Four Books, Serlio, Durand, Piranesi, Letarouilly, historic competition drawings, early modernist plates.""",
    "thennow": """a place with an archival photograph (public domain, usually before 1960) and a recent photo of the same place from a similar viewpoint, both on Wikimedia Commons. The story is what happened in between: a demolition, a war, a flood, a rebuilding, a loss that still shapes the city. a = then (role "then"), b = now (role "now"). The two photos should show the same view as closely as possible.""",
    "place": """a painting of a real, identifiable place (public domain, large image on Commons) and a recent photo of that place from a similar viewpoint (Commons, free licence). The story is what the painter changed, left out or invented — or what the place became. a = the painting (role "painted"), b = the photo (role "real").""",
    "film": """a film scene that quotes a painting — a documented influence (the director or cinematographer said so, or it is a well-established reading). a = the painting (public domain, large image on Commons; role "painting"), b = the film (role "film"): give film.title and film.year; for films in the public domain also a Commons query for a still. The story is why the filmmaker borrowed this image and what it means in the film.""",
    "layer": """a painting in which technical imaging (X-ray, infrared reflectography, multispectral scans) revealed something hidden underneath — a different figure, a changed composition, another painting — where BOTH the painting and the technical image are on Wikimedia Commons. a = the painting as we see it (role "visible"), b = the technical image (role "beneath"). The story is what the painter hid or changed, and why.""",
    "which": """two versions of one work that a viewer can compare: two versions by the same artist, an original and a copy, a work before and after restoration, a study and the final painting. Both large on Wikimedia Commons. The reel asks the viewer a question — which one is the original, the later one, the one that was cut, the one that was repainted — shows the differences, and answers at the end. a and b in the order they are shown (role "A" and "B"); answer — "a" or "b".""",
}


def pair_pick_system(kind: str) -> str:
    return f"""You pick material for an AHMAG Instagram reel built on two images and the moment one turns into the other. Pick {PAIR_PICK[kind]}

It must have a documented story with tension that a stranger would care about in two seconds. Check facts with web search. Prefer pairs that are not the most overexposed. Not from the avoid list.

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "short name for the reel, e.g. Villa Rotonda", "a": {{"role": "...", "title": "...", "author": "...", "year": "...", "museum": "museum or place, city", "commons": "query for Wikimedia Commons search"}}, "b": {{"role": "...", "title": "...", "author": "...", "year": "...", "museum": "...", "commons": "query for Wikimedia Commons search", "film": {{"title": "only for a film", "year": "..."}}}}, "answer": "a|b — only for a which-one reel", "facts": ["6–10 specific verified facts in English"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}"""


PAIR_NEED = {
    "drawing": "the historical drawing, plate or engraving itself, whole and clean, not a photo of a book spread if a clean scan exists",
    "built": "a clear, well-composed photo of the whole building, ideally from the same side as the drawing",
    "then": "an old archival photograph of the place, clean scan",
    "now": "a recent colour photo of the same place from a similar viewpoint",
    "painted": "the painting itself, whole, straight, no frame",
    "real": "a recent photo of the same place, ideally from the viewpoint of the painting",
    "painting": "the painting itself, whole, straight, no frame",
    "film": "a clean still frame from the film, no text or poster, the scene that quotes the painting",
    "visible": "the painting itself, whole, straight, no frame",
    "beneath": "the X-ray, infrared or technical image of this painting, whole",
    "A": "the work itself, whole, straight, no frame",
    "B": "the work itself, whole, straight, no frame",
}

PAIR_EXAMPLE = {
    "plan": """  hook: "He drew this villa in *1570*." (show a, the drawing appears line by line)
  context: "Four identical fronts. Nobody had built a house like that."
  reveals: "Look at the ^plan. A circle inside a square, and a dome over the *circle*." (show a, box on the plan) / "The drawing gives it a tall dome. What they ^built is *lower*." (show b)
  climax: "The house is still almost line for *line*." (show both)
  final: "He died before it was finished. It's been copied from Virginia to Petersburg *since*." (show b)""",
    "thennow": """  hook: "This station had a *ceiling* of glass." (show a)
  context: "Eighty-four columns. People came to look at it, not just to travel."
  reveals: "In ^1963 the owners sold the air above it." (show a, box) / "So it came ^down. It took three *years*." (show b) / ...
  climax: "The trains still run. *Underground*." (show both)""",
    "place": """  hook: "He painted this café at *night*. Without black." (show a)
  reveals: "Here's the same ^square today. The awning is still *yellow*." (show b) / "But the ^stars are gone. The city lights *won*." (show both) ...""",
    "film": """  hook: "This *tower* was painted in 1563." (show a)
  reveals: "Fritz Lang put it in ^Metropolis, three hundred and sixty years later." (show b) / "Same spiral. Same ^crowd of workers at its *foot*." (show both) ...""",
    "layer": """  hook: "There's a man under this *painting*." (show a)
  reveals: "The ^X-ray shows him. An officer, in full uniform." (show b) / ...
  climax: "He painted over a *general*." (show both)""",
    "which": """  hook: "One of these was painted twenty years *later*. Which?" (show both)
  reveals: "Look at the angel's ^hand on the left. It *points*." (show a, box) / "On the right the ^hand is gone. And there are *halos*." (show b, box) / ...
  climax: "The one on the *right*." (show both — the answer; set "pick")""",
}


def pair_story_system(kind: str) -> str:
    roles = KINDS[kind]["roles"]
    which = kind == "which"
    pick = ', "pick": "a|b — the answer, only in the climax"' if which else ""
    return f"""You write the narration for an AHMAG Instagram reel built on two images: image A ({roles[0].lower()}) and image B ({roles[1].lower()}), attached in that order. A narrator reads the story aloud, the words appear on screen as they are spoken, and the screen shows A, B, or both. Coordinates are fractions of the width and height of the image they refer to (0 to 1, from its top-left corner).

{ENGINE_RULES}

The turn from A to B is the key moment of the reel: the story must earn it. Show B for the first time on the line where the story needs it, and mark with ^ the word at which B appears.

Example of the structure (do not reuse its lines):
{PAIR_EXAMPLE[kind]}

Structure — 25 to 35 seconds, 70–90 words in total:
1. {HOOK_RULES} Each hook has show and box.
2. context — up to 14 words; raises the stakes, does not explain.
3. reveals — 3 steps (4 only if needed), each up to 18 words; label — 1–3 words naming what we look at.
4. climax — up to 12 words: the answer to the hook's question. One brief human note.
5. final — up to 20 words: the last turn; echoes the hook so the loop back to the start feels natural.

For every part: show — "a", "b" or "both" ({'both images one above the other' if KINDS[kind]['layout'] == 'split' else 'the two images split by a line, half and half'}); box — [x0, y0, x1, y1] of a detail in the image named by show, to move the camera there, or null for the whole image; with "both" the box is always null. target — 2–6 words naming exactly what is inside the box. {"The hook and the climax show both; the reveals go back and forth between a and b to show the differences." if which else "Start on A. Show B for the first time on the line that earns it; after that the story may go back to A and show both, but end on B or both."}

{TAIL_RULES}

caption — the Instagram caption WITHOUT the hook: first line — what the two images are, with years and authors; then 2–3 short paragraphs with the story and one or two facts that did not fit the video; last line — where both can be seen.

Return ONLY JSON:
{{"hooks": [{{"type": "contradiction|hidden|stakes|challenge|question", "text": "...", "show": "a|b|both", "box": null, "target": "...", "delivery": "..."}}], "hook_pick": 0, "context": {{"text": "...", "show": "a|b|both", "box": null, "target": "...", "delivery": "..."}}, "reveals": [{{"text": "...", "show": "a|b|both", "box": [0.1, 0.2, 0.3, 0.5], "target": "...", "label": "...", "delivery": "..."}}], "climax": {{"text": "...", "show": "a|b|both", "box": null, "target": "...", "delivery": "..."{pick}}}, "final": {{"text": "...", "show": "a|b|both", "delivery": "..."}}, "voice_direction": "...", "caption": "...", "hashtags": ["..."]}}"""
