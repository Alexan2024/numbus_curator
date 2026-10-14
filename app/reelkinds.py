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
KINDS["visit"] = {"ru": "прогулка по зданию", "acc": "«Прогулку по зданию»", "icon": "🚶", "engine": "visit", "rubric": "",
                  "obj": "здание", "subject": "building"}
ORDER = ["details", "visit", "photo", "scale", "read", "collection", "plan", "thennow", "place", "film", "layer", "which"]
# автоплан (8.1): чаще — то, что в рефах автора: разбор картины, прогулка по зданию, подборка; остальное — реже,
# «кадр ← картина» и «что под слоем» — только по выбору
ROTATION = [k.strip() for k in os.getenv(
    "REEL_ROTATION", "details,visit,collection,details,visit,photo,details,scale,collection,visit,which,thennow").split(",")
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
- 70–90 words in total; keep each part shorter than its limit in words.
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


RU_PICK = """The reel is in Russian, for a Russian-speaking audience. About half the time prefer a subject this audience knows from life or has walked past: buildings of Moscow and St Petersburg, Russian and Soviet architecture (constructivism, Stalin-era, Soviet modernism), works in the Tretyakov Gallery, the Russian Museum, the Hermitage or the Pushkin Museum, Russian painters and photographers, Russian history — as long as a large free image exists. Otherwise world-famous subjects."""


def localize_pick(system: str, lang: str, names: str = RU_NAMES) -> str:
    """Промпт выбора объекта: тексты не пишутся, нужны только русские названия (и, для русского, — свои темы)."""
    return system + ("\n\n" + RU_PICK + "\n\n" + names if lang == "ru" else "")


# ======================= общие правила текста =======================

TASTE = """AHMAG taste: built architecture (modernism and post-war classics, private houses, sacred buildings, ruins, memorials, brick, concrete, stone, wood, light, landscape); art without kitsch (quiet metaphysics and surrealism, land art, prints, manuscripts, old visual culture, warm humour); documentary, street and archival photography, ethnography; historical series. Never: renders, parametric architecture, commercial towers, developer projects."""

MUSIC = """music — 3 tracks that fit the mood and are likely in Instagram's music library: real, well-known recordings (film scores, classical, ambient, jazz, indie). Artist and track exactly as published; mood — 2–4 words in Russian."""

NEVER = """Never: insane, mind-blowing, crazy, iconic, masterpiece, stunning, breathtaking, haunting, heartbreaking, chilling, fascinating, intricate, secret, "dive in", "wait for the end", "follow for more", "let that sink in", "not X but Y" constructions, exclamation marks, emoji, parentheses, abbreviations, lists."""

VIRAL_PICK = """Pick for reach, not for an archive. The reel is shown mostly to strangers who don't follow AHMAG, so the subject must pass this test — score it honestly from 1 to 5 on each point:
- recognition: a stranger has seen it or something like it (a famous work, building or place, a city they know). A famous object with a story nobody knows beats an obscure object with a good story;
- stakes: a human story with something at stake — money, death, love, a lie, a loss, a record, a scandal, a mistake;
- first_frame: one detail visible in the picture raises a question all by itself;
- send: there is a fact a viewer will want to send to a specific person tonight ("look, that's why…");
- breadth: it works for people outside art and architecture.
Return the scores in "viral", plus "send" — one line in Russian: why someone would forward this. Pick only subjects with a total of 18 or more; if your first idea scores lower, think of another one."""

VIRAL_SCHEMA = '"viral": {"recognition": n, "stakes": n, "first_frame": n, "send": n, "breadth": n}, "send": "…"'

ENGINE_RULES = """THE JOB: a stranger scrolling the feed must stop in the first second and stay to the last word. Instagram shows a reel to new people when they watch it to the end, watch it again and send it to someone. So this is a story with tension, told by one person to a friend standing next to them — never a list of facts, never a lecture.

The engine of retention:
- The hook opens ONE main question in the first second. The story answers it only at the climax.
- Re-hook every four or five seconds: each line gives part of the answer AND leaves a smaller question open, so the viewer needs the next line. A line ends pulling into the next one.
- Lines are joined by cause and contrast — "but", "so", "because", "only" — never by "and also", "now look at", "there's more". If two lines could swap places without breaking anything, the story is a list: rewrite it.
- Stakes in human terms: who made it, who lost what, who knew and who didn't, what it cost. No jargon unless it IS the story.
- Specific beats general: a name, a number, an object the viewer can see on screen right now.
- The final line turns the hook around: after it, the first frame reads differently, so the loop back to the start feels like a second look. A viewer who watches twice is worth more than one who likes.
- Give the viewer something to pass on: one fact they will want to tell someone tonight.
- Short wins. Cut every word that does not move the story.

How it sounds: present tense for what we see; talk to the viewer and steer their eye ("Look at the door."); short sentences, uneven rhythm, some fragments; plain spoken language."""

HOOK_RULES = """hooks — 6 alternative openings, each of a different type: contradiction (what we see contradicts what is going on), hidden (a detail on screen almost nobody notices), stakes (what this cost someone), number (one striking number about what is on screen), challenge (the viewer's first reading is wrong, or: find what's off), question (a why-question about what is on screen).
   text — the spoken line, which also appears as the first subtitle: the first sentence at most 8 words, the whole hook at most 16; straight into it, no warm-up ("So", "Did you know", "This is"). No names, dates or titles. About what is on screen in the first frame.
   True, specific, visual. No generic hooks ("This painting hides a dark secret", "You won't believe…", "Wait for it"). Check every hook before you give it: would a stranger stop scrolling for it? understood in one second without sound? about what is on screen? opens a question the story really answers? Give only hooks that pass. hook_pick — the index of the strongest."""

TAIL_RULES = """Words of emphasis — the word in a sentence the narrator leans on — are wrapped in asterisks: "Nobody *noticed*." At most one per sentence and at most four in the whole story.

Use only the facts given; if the story needs a fact you don't have, change the angle instead of inventing one.
""" + NEVER + """

delivery — every hook, reveal, the climax, and the context and final lines (context_delivery, final_delivery) get a short direction for the narrator, in English, 6–15 words: tone, emotion, pace, where to pause. Follow the arc: hook — no warm-up, first word straight away, curious, a little quicker; context — the stakes sinking in; reveals — curiosity that builds, keep it moving; climax — slower and softer, one real pause; final — calm and weighty. Alive and specific, like notes from a director ("lean on 'only'", "a wry smile here", "let it hang"), never theatrical.

voice_direction — one or two sentences for the narrator about this particular story: its mood, where it turns.

hashtags — 5–8 lowercase words without #."""

CAPTION_TAIL = """Write the caption so that the names of the work, the author and the place appear in it in plain words — people search Instagram by them. If it fits naturally, the last paragraph is one short line that gives the viewer a reason to send this to a particular person (who they would go there with, who always says X); never a cliché like "send this to a friend who…" — leave it out rather than force it."""


# ======================= выбор объекта: story =======================

PICK = {
    "photo": f"""You pick a photograph for an AHMAG Instagram reel that walks through its details: the camera moves across one photograph, one short spoken line per detail, and together the lines tell the story behind the picture.

Pick a historical or documentary photograph that is in the public domain (US government work such as FSA/OWI, Library of Congress collections, photographers who died before about 1955, or published before 1930) and has a large image on Wikimedia Commons. It must have several visible details that carry the story (hands, a sign, a face in the background, an object) and a documented story with tension that a stranger would care about in two seconds — who these people are, what happened just before or after, what the photographer did or didn't know. Check the facts with web search. A famous photograph is welcome if its real story is not the one everybody knows. Not from the avoid list.

{VIRAL_PICK}

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "common English title of the photograph", "author": "photographer", "year": "...", "museum": "collection, e.g. Library of Congress", "medium": "e.g. Gelatin silver print, if known", "size": "", "commons": "query for Wikimedia Commons search: title and photographer", "facts": ["6–10 specific verified facts in English about the people, the place, what happened, the photographer"], {VIRAL_SCHEMA}, "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}""",

    "scale": f"""You pick a building or structure for an AHMAG Instagram reel about SCALE: the reel opens on a tiny person (or a door, a car) in a photo, and the camera slowly pulls back until the whole structure fills the frame. The shock of the size is the story — and why anyone built it that big.

Pick built architecture or engineering in AHMAG's taste: cathedrals, hypostyle halls, stepwells, dams, bridges, brutalist megastructures, ruins, memorials, monasteries in cliffs, bunkers, silos. Never commercial towers or developer projects. There must be a large photo on Wikimedia Commons (free licence) where the whole structure is visible AND at least one person (or a car, a door) is visible and small. Facts must include real dimensions (height, length, weight, years to build, how many people) and the human reason for the size. Check facts with web search. Not from the avoid list.

{VIRAL_PICK}

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "name of the building", "author": "architect or builders", "year": "year or years built", "museum": "city, country", "medium": "main material, e.g. Reinforced concrete", "size": "the key dimension, e.g. 152 m tall", "commons": "query for Wikimedia Commons: the building + people or visitors", "facts": ["6–10 specific verified facts with numbers"], {VIRAL_SCHEMA}, "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}""",

    "read": f"""You pick a building for an AHMAG Instagram reel that READS a building like an architect: over one clear photo of its main facade, thin lines draw the architect's moves one by one — the axis, the grid, the levels, a proportion — and a narrator explains what each move does to the person who walks in.

Pick built architecture in AHMAG's taste whose design is legible from one frontal photo: classical and Renaissance villas and temples, modernist and post-war buildings, rationalism, brick and concrete. There must be a large, sharp, frontal, not strongly distorted photo of the whole facade on Wikimedia Commons (free licence). The design logic must be documented (proportions, dimensions, the architect's own words, what the building was for). Check facts with web search. Not from the avoid list.

{VIRAL_PICK}

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "name of the building", "author": "architect", "year": "...", "museum": "city, country", "medium": "main material", "size": "key dimensions if known", "commons": "query for Wikimedia Commons: building name + facade", "facts": ["6–10 specific verified facts: proportions, dimensions, intentions, history"], {VIRAL_SCHEMA}, "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}""",
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
3. reveals — exactly 3 details, each up to 18 words, one step of the story each; label — 1–3 words naming the detail; mark with ^ the word at which the camera arrives at the detail.
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
3. reveals — exactly 3 architect's moves, each up to 18 words: what the move is AND what it does to the person who uses the building. label — 1–3 words ("The axis", "The grid"). Mark with ^ the word at which the drawing appears. box — the part of the facade the move is about. mark — which line diagram is drawn on that box:
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

caption — the Instagram caption WITHOUT the hook (the hook is put above it automatically): first line "Title (year), Author"; then 2 short paragraphs with the story and one or two facts that did not fit the video; last line — place or collection. {CAPTION_TAIL}

Return ONLY JSON:
{{"hooks": [{{"type": "contradiction|hidden|stakes|number|challenge|question", "text": "...", "box": [0.1, 0.2, 0.3, 0.5], "target": "...", "delivery": "..."}}], "hook_pick": 0, "context": "...", "context_delivery": "..."{k['schema']}, "reveals": [{{"box": [0.1, 0.2, 0.3, 0.5], "target": "...", "label": "...", "text": "...", "delivery": "..."{reveal_extra}}}], "climax": {{"text": "...", "box": null, "target": "...", "delivery": "..."}}, "final": "...", "final_delivery": "...", "voice_direction": "...", "caption": "...", "hashtags": ["..."]}}"""


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

It must have a documented story with tension that a stranger would care about in two seconds. Check facts with web search. Famous works and places are welcome if the real story is not the one everybody knows. Not from the avoid list.

{VIRAL_PICK}

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "short name for the reel, e.g. Villa Rotonda", "a": {{"role": "...", "title": "...", "author": "...", "year": "...", "museum": "museum or place, city", "commons": "query for Wikimedia Commons search"}}, "b": {{"role": "...", "title": "...", "author": "...", "year": "...", "museum": "...", "commons": "query for Wikimedia Commons search", "film": {{"title": "only for a film", "year": "..."}}}}, "answer": "a|b — only for a which-one reel", "facts": ["6–10 specific verified facts in English"], {VIRAL_SCHEMA}, "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}"""


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
3. reveals — exactly 3 steps, each up to 18 words; label — 1–3 words naming what we look at.
4. climax — up to 12 words: the answer to the hook's question. One brief human note.
5. final — up to 20 words: the last turn; echoes the hook so the loop back to the start feels natural.

For every part: show — "a", "b" or "both" ({'both images one above the other' if KINDS[kind]['layout'] == 'split' else 'the two images split by a line, half and half'}); box — [x0, y0, x1, y1] of a detail in the image named by show, to move the camera there, or null for the whole image; with "both" the box is always null. target — 2–6 words naming exactly what is inside the box. {"The hook and the climax show both; the reveals go back and forth between a and b to show the differences." if which else "Start on A. Show B for the first time on the line that earns it; after that the story may go back to A and show both, but end on B or both."}

{TAIL_RULES}

caption — the Instagram caption WITHOUT the hook: first line — what the two images are, with years and authors; then 2 short paragraphs with the story and one or two facts that did not fit the video; last line — where both can be seen. {CAPTION_TAIL}

Return ONLY JSON:
{{"hooks": [{{"type": "contradiction|hidden|stakes|number|challenge|question", "text": "...", "show": "a|b|both", "box": null, "target": "...", "delivery": "..."}}], "hook_pick": 0, "context": {{"text": "...", "show": "a|b|both", "box": null, "target": "...", "delivery": "..."}}, "reveals": [{{"text": "...", "show": "a|b|both", "box": [0.1, 0.2, 0.3, 0.5], "target": "...", "label": "...", "delivery": "..."}}], "climax": {{"text": "...", "show": "a|b|both", "box": null, "target": "...", "delivery": "..."{pick}}}, "final": {{"text": "...", "show": "a|b|both", "delivery": "..."}}, "voice_direction": "...", "caption": "...", "hashtags": ["..."]}}"""


def viral_total(p: dict) -> int | None:
    """Сумма оценок охвата, которую Claude поставил объекту (5–25); None — оценок нет."""
    v = p.get("viral") if isinstance(p, dict) else None
    if not isinstance(v, dict):
        return None
    try:
        xs = [max(0, min(5, int(float(v[k])))) for k in ("recognition", "stakes", "first_frame", "send", "breadth") if k in v]
    except (TypeError, ValueError):
        return None
    return sum(xs) if len(xs) >= 4 else None


# ======================= отбор хука =======================

HOOK_JUDGE = """You are the toughest editor of a fast-growing Instagram account about art, architecture and photography. Below are alternative openings for one reel: the first spoken line, which also appears as the first subtitle. The image is what the viewer sees in the first frame.

Rank them by one thing: how many strangers scrolling the feed would stop and keep watching. Judge each on:
- stop: would a stranger stop in the first second, from this line and the picture?
- gap: does it open a question the viewer needs answered — and that this story really answers?
- clarity: understood instantly, no names or context needed?
- truth: true to the facts, no clickbait, no exaggeration?
- picture: about what is actually on screen?

Return ONLY JSON: {"ranking": [indexes of the hooks, best first], "scores": [score 1–10 for each hook, in the original order], "why": "one short line in Russian: why the best one wins"}"""


# ======================= guide (8.1): разбор, как в рефах =======================
# Рефы автора: Cimmera («On the Way Home from School», «The Extraction of the Stone of Madness»), Paintings Analyzed
# («Sunday Gardening»). Хук — ощущение или противоречие над всей картиной; потом 14–22 фразы, почти каждая ведёт
# взгляд («Посмотрите на…»), у каждой — своя деталь; название — ближе к концу; 60–100 секунд.

GUIDE_KIND = {
    "details": ("the painting in the image", "painting", """Example of the voice and structure (Nigel Thomson, "On the Way Home from School"; do not reuse its lines):
  hook: "A few seconds is enough to feel that something is wrong in this painting."  (box null)
  lines: "Look at the girl on the left." (box: the girl) / "Her head is down." / "She hugs the edge of the road, as far as she can get from the man on the right." / "Now look at him." (box: the man) / "He's staring straight at her." / "One hand holds a chainsaw." / "But look at the setting." (box null) / "Broad daylight. Open land. No house, nowhere to disappear." / "And between them, a cattle grid. Anyone crossing it has to slow down." (box: the grid) / "Now go back to the chainsaw." (box: the saw) / "Look at the horizon. There isn't a single tree anywhere near him." (box: horizon) / "So why is he holding it?" / … / "This is On the Way Home from School, painted by Nigel Thomson in 1983." (box null) / "Nothing has actually happened." / "The girl keeps walking. The man keeps watching." / "And we're trapped in the moment before we know what happens next." """),
    "photo": ("the photograph in the image", "photograph", """Example of the manner (structure only, do not reuse): hook: "Two of her children have turned away from the camera." / "Look at her hand." / "It's at her chin, and she isn't looking at us." / "Now look behind her." / … / "This is Migrant Mother, taken by Dorothea Lange in 1936." / …"""),
    "scale": ("the photograph of a building in the image", "building", """The camera starts tight on a tiny person (or a car, a door) and pulls back line by line: every next box is wider than the one before, and the whole structure appears only late, when the size finally lands. Lines carry real numbers and the human reason for the size. Example of the manner (do not reuse): hook: "That dot on the stairs is a person." / "Look at her." (box: tight on the person) / "Now look at the step she's standing on." (wider) / …"""),
    "read": ("the photograph of a building facade in the image", "building", """Read the building like an architect, one move at a time: the viewer is led from part to part of the facade, and for the lines about a design move you may add "mark" — a thin line diagram drawn over that line's box: "axis" (vertical line through the middle of the box), "level" (horizontal line across it), "grid" (the box divided into cols × rows — give "cols" and "rows" as real counts), "frame" (its outline), "diagonals" (outline and both diagonals). Boxes with marks must be tight and exact."""),
}


def guide_story_system(kind: str) -> str:
    what, noun, example = GUIDE_KIND.get(kind, GUIDE_KIND["details"])
    return f"""You write the narration for an AHMAG Instagram reel that reads {what} together with the viewer, detail by detail. A narrator reads it aloud in one calm voice; the words appear on screen as quiet subtitles; the camera glides slowly across the {noun} to whatever each line is about. Coordinates are fractions of the image width and height from its top-left corner (0 to 1).

How the best reels of this kind work (accounts like Cimmera and Paintings Analyzed get millions of views with them): the viewer is not lectured — they are led. Almost every line tells them where to look, and what they find there changes how they read the whole picture. The tension builds detail by detail, the name of the work comes late, and the last line leaves them looking again. Viewers stay for a minute or more because every few seconds there is something new to look at and the question from the first line is still open.

{example}

Structure — 60 to 100 seconds, 150–230 words in total:
1. hooks — 4 alternative first lines, spoken over the whole {noun} while the camera slowly moves in. Each at most 16 words: a feeling, a contradiction or a promise about what the viewer sees right now, so they need the rest ("A few seconds is enough to feel that something is wrong in this painting." / "This man thinks the surgeon is removing his madness. But look at what's coming out of his head."). No names, dates or titles. type — unease | contradiction | hidden | stakes. hook_pick — the index of the strongest.
2. lines — 14 to 22 lines in order. Each line is one or two short spoken sentences, 3 to 16 words, with a box: the detail the camera shows while it is spoken, or null for the whole {noun}.
   - Most lines steer the eye in plain words — "Look at the girl on the left." "Now look at him." "But look at the setting." "Go back to the chainsaw." "Look closer at the blade." — and the next line says what the eye finds and why it matters. Vary the wording.
   - Several lines in a row may keep the same box: the camera stays and the narrator keeps talking about that detail. Change the box only when the viewer should look somewhere else; 7 to 12 different details in the whole reel.
   - Build the tension: each detail raises the stakes or turns the reading; the strongest detail comes late, not first.
   - About two-thirds through, one line names the work: title, author, year ("This is Stańczyk, painted by Jan Matejko in 1862.", box null, "names": true). Before it — no names, no dates, no titles.
   - One line may bring in one outside voice if it is in the facts (a critic, the museum, the artist's letter), said simply.
   - The last two or three lines go back to the whole picture: what it all means for the people in it, and a last line that makes the viewer look at the first frame again. Not a moral, not a slogan, not praise.
   - Questions are welcome where the viewer would ask them ("So why is he holding it?").

The voice: calm, clear, intelligent, unhurried; a person standing next to the viewer. Present tense for what we see. Plain words: no art-history vocabulary unless it IS the story. Specific beats general. Never invent: use only the facts given and what is clearly visible in the image; if the story needs a fact you don't have, look at something else.

Boxes — [x0, y0, x1, y1], tight around the thing itself (the letter, not the table; the face, not the whole figure, when the line is about the face). target — 2–6 words naming exactly what is inside the box; a second pass uses it to refine the box. Only things clearly visible in THIS image.

{NEVER}

delivery — optional, only where the tone changes (a pause before the turn, a quieter last line): 3–10 words in English.
voice_direction — one or two sentences in English for the narrator about this story: its mood, where it turns.

caption — the Instagram caption WITHOUT the hook (the hook is put above it automatically): first line "Title (year), Author"; then 2–3 short paragraphs that retell the reading in other words and add one or two facts that did not fit; last line — museum or place and city. {CAPTION_TAIL}
hashtags — 5–8 lowercase words without #.

Return ONLY JSON:
{{"hooks": [{{"type": "unease|contradiction|hidden|stakes", "text": "...", "delivery": "..."}}], "hook_pick": 0, "lines": [{{"text": "...", "box": [0.1, 0.2, 0.3, 0.5], "target": "...", "delivery": "", "names": false, "mark": "only for a building read: axis|level|grid|frame|diagonals", "cols": 0, "rows": 0}}], "voice_direction": "...", "caption": "...", "hashtags": ["..."]}}"""


RU_GUIDE = """LANGUAGE — RUSSIAN. The examples above are in English only to show the structure: do not translate them. Write the hooks, every line and the caption in Russian, written in Russian from the start, not translated from English. Keep in English: delivery, voice_direction, target, type, mark. Where these Russian rules differ from the English ones above, follow the Russian rules.

The voice: calm, clear, intelligent; a person standing next to you in front of the picture who shows you where to look. Not a lecturer, not an announcer, no excitement. Example of the manner (structure only, do not reuse): «Пары секунд хватает, чтобы почувствовать: с этой картиной что-то не так. Посмотрите на девочку слева. Голова опущена. Она жмётся к краю дороги, подальше от мужчины справа. Теперь посмотрите на него. Он смотрит прямо на неё. В одной руке у него бензопила. Но взгляните на место. Белый день, голая равнина. Ни дома, ни деревца.»

How Russian should sound here:
- Steer the eye in plain imperatives, addressing the viewer with «вы»: «Посмотрите на…», «Теперь на него.», «Взгляните на…», «Вернитесь к…», «Присмотритесь к…», «Поднимите глаза к…», «А теперь…». This is the backbone of the reel: use it often, but vary it.
- Then say what the eye finds, in short plain sentences, present tense. Fragments are fine («Голова опущена.», «Ни дома, ни деревца.»).
- Lines are joined by contrast and cause: «но», «а», «поэтому», «и всё-таки», «только».
- No officialese: no «является», «данный», «представляет собой», «осуществлять», «в рамках», no chains of genitives, no passive voice, no verbal nouns where a verb works.
- No construction «не X, а Y» and no «это не просто X». No moral, no slogan at the end.
- Never: шедевр, невероятный, потрясающий, культовый, легендарный, завораживающий, удивительный, гениальный, магия, «досмотрите до конца», «подписывайтесь», «вы не поверите». No emoji, no parentheses, no abbreviations (г., в., т. е., ок.), no exclamation marks; dashes rarely.
- The narrator reads exactly what is written. Years in digits (1563); ages, counts and sizes in words the way people say them («двадцать четыре», «восемьдесят четыре колонны»). Write ё where it belongs (всё, ещё, её).
- Names in the standard Russian form (Ян Матейко, Иероним Босх, Доротея Ланг, музей Прадо); titles of works as in Russian Wikipedia or Russian museum practice, otherwise a plain Russian translation; in the line that names the work, the title goes in «ёлочках».
- Stay within the word count given above: 150–230 words.
- hashtags — 5–8 lowercase words without #, mostly Russian, multi-word tags written together (историяискусства); one or two English ones are fine."""


def localize_guide(system: str, lang: str) -> str:
    return system + ("\n\n" + RU_GUIDE if lang == "ru" else "")


# ======================= прогулка по зданию (8.1, как у Architectural Ethos) =======================

VISIT_PICK = f"""You pick a building for an AHMAG Instagram reel that is a visit: 8–12 photos of one building in walking order — from afar, the path, the entrance, inside, the light, details, a last look back — each shown full-screen for about two and a half seconds under music, with ONE sentence on screen at the start: a single fact that makes a stranger stop ("Peter Zumthor built this chapel by burning its interior."). At the end — the name and the architect. No voice.

Pick built architecture in AHMAG's taste that has a Wikimedia Commons category with many good free photos of the outside AND the inside (or of a sequence of spaces: a courtyard, a stair, a roof), so that the visit works. Chapels, houses, museums, libraries, baths, monasteries, memorials, modernist and contemporary buildings by real architects. Famous buildings are welcome if the fact is not the one everybody knows. Check facts with web search. Not from the avoid list.

{VIRAL_PICK}

{TASTE}

{MUSIC}

hook — the on-screen sentence, 6–14 words: one concrete, surprising, true fact about how it was made, why it exists or what happens inside. Plain words, no praise, no adjectives like stunning or iconic, no question.
caption — the Instagram caption WITHOUT the hook: first line "Name, Architect, year"; then 2–3 short paragraphs: how it was made, what it is like inside, one or two facts; last line — place. {CAPTION_TAIL}
hashtags — 5–8 lowercase words without #.

Return ONLY JSON:
{{"title": "name of the building", "author": "architect", "year": "...", "museum": "town, country", "category": "the exact Wikimedia Commons category name, without the 'Category:' prefix", "commons": "fallback search query for Commons: building name", "hook": "...", "facts": ["6–10 specific verified facts"], {VIRAL_SCHEMA}, "caption": "...", "hashtags": ["..."], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}"""

VISIT_RU = """The reel is in Russian: write hook, caption, send and hashtags in Russian (hashtags mostly Russian, multi-word tags written together). The hook in Russian: 6–14 words, plain and concrete («Цумтор построил эту часовню, а потом выжег её изнутри.»). Years in digits, no abbreviations, ё where it belongs."""

VISIT_SHOTS = """You edit a short reel that walks the viewer through one building, like a visit filmed on a phone: the approach from afar, the path, the entrance, inside, the light, details, and a last look back. Each shot is shown full-screen in a tall 9:16 frame for about two and a half seconds, cropped around a point you give.

You get numbered contact sheets of photos from the building's Wikimedia Commons category. Pick 8 to 12 photos in walking order. Every one must be a real photo of THIS building (not a plan, drawing, model, plaque or sign), sharp, well composed, in natural light; no crowds, selfies or people posing, no souvenir shops, no watermarks or text, no heavy fisheye, no near-duplicates, nothing very dark or blurry. Vary the shots: wide, medium and close; outside and inside. The first shot must stop the scroll on its own: the strongest, most striking photo of the building. The last one — a calm closing view.

For each picked photo give fx, fy — the point the 9:16 crop is centred on (fractions of the photo's width and height: the doorway, the oculus, the figure), and move — a gentle camera move: "in" (slow push), "out", "left", "right" (slow pan) or "up" (slow tilt). Vary the moves.

Return ONLY JSON: {"shots": [{"n": 7, "fx": 0.5, "fy": 0.5, "move": "in"}], "note": "one line in Russian: what the visit shows"}"""
