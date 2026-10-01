"""NUMBUS Branding — тексты интерфейса (RU / EN).

Разметка — HTML (parse_mode="HTML"). Пользовательские данные (название
бренда, хештеги) экранируются в bot.py перед подстановкой.
"""

T = {
    # ---------- Общие кнопки ----------
    "b_back":     {"ru": "⬅️ Назад", "en": "⬅️ Back"},
    "b_menu":     {"ru": "⬅️ В меню", "en": "⬅️ Menu"},

    # ---------- Старт и доступ ----------
    "welcome_new": {
        "ru": "👋 Это <b>NUMBUS Branding</b> — фирменный стиль для ваших публикаций за секунды.\n\n"
              "Присылаете фото — получаете готовые посты с вашим логотипом и хештегом, "
              "обложки и сторис для Instagram и Telegram.\n\n"
              "Сейчас доступ по приглашениям. Пришлите инвайт-код:",
        "en": "👋 This is <b>NUMBUS Branding</b> — your brand identity on every post, in seconds.\n\n"
              "Send photos — get ready-to-publish posts with your logo and hashtag, "
              "plus covers and stories for Instagram and Telegram.\n\n"
              "Access is invite-only right now. Send your invite code:",
    },
    "ask_code":   {"ru": "Пришлите инвайт-код (формат <code>NB-XXXX-XXXX</code>):",
                   "en": "Send your invite code (format <code>NB-XXXX-XXXX</code>):"},
    "code_bad":   {"ru": "Код не подошёл или уже использован. Проверьте и пришлите ещё раз.",
                   "en": "This code is invalid or already used. Check it and try again."},
    "code_ok":    {"ru": "✅ Код активирован: тариф <b>{plan}</b> до {until}.\n\nСоберём бренд-кит — это пара минут.",
                   "en": "✅ Code activated: <b>{plan}</b> plan until {until}.\n\nLet's build your brand kit — takes a couple of minutes."},
    "join_ok":    {"ru": "✅ Вы в команде <b>{brand}</b>. Можно делать посты.",
                   "en": "✅ You've joined <b>{brand}</b>. You can start making posts."},
    "join_bad":   {"ru": "Ссылка-приглашение недействительна. Попросите владельца бренда прислать новую.",
                   "en": "This invite link is no longer valid. Ask the brand owner for a new one."},
    "join_full":  {"ru": "В команде <b>{brand}</b> уже максимум участников для текущего тарифа.",
                   "en": "<b>{brand}</b> has reached the member limit for its plan."},
    "join_notify": {"ru": "👥 {who} присоединился к команде <b>{brand}</b>.",
                    "en": "👥 {who} joined the <b>{brand}</b> team."},

    # ---------- Главное меню ----------
    "menu_head":  {"ru": "<b>NUMBUS Branding</b> · {brand}", "en": "<b>NUMBUS Branding</b> · {brand}"},
    "menu_plan":  {"ru": "Тариф: {plan} · до {until}\nФото в этом месяце: {used} из {limit}",
                   "en": "Plan: {plan} · until {until}\nPhotos this month: {used} of {limit}"},
    "menu_expired": {"ru": "⚠️ Срок тарифа истёк {until}. Напишите {support}, чтобы продлить.",
                     "en": "⚠️ Your plan expired on {until}. Contact {support} to renew."},
    "menu_no_kit": {"ru": "Бренд-кит ещё не собран — начните с него.",
                    "en": "Your brand kit isn't set up yet — start there."},
    "b_team":     {"ru": "👥 Команда", "en": "👥 Team"},
    "b_switch":   {"ru": "🔁 Сменить бренд", "en": "🔁 Switch brand"},
    "b_code":     {"ru": "➕ Ввести код", "en": "➕ Enter code"},
    "b_lang":     {"ru": "🌐 English", "en": "🌐 Русский"},
    "switch_head": {"ru": "Выберите бренд:", "en": "Choose a brand:"},

    # ---------- Мастер бренд-кита ----------
    "step":       {"ru": "<i>Шаг {n} из 3</i>\n", "en": "<i>Step {n} of 3</i>\n"},
    "ask_name":   {"ru": "<b>Название</b>\nКак называется бренд или канал?",
                   "en": "<b>Name</b>\nWhat's the name of your brand or channel?"},
    "name_bad":   {"ru": "Название должно быть от 1 до 40 символов.",
                   "en": "The name must be 1 to 40 characters."},
    "ask_logo":   {"ru": "<b>Логотип</b>\nПришлите логотип <b>файлом</b> (скрепка → Файл).\n\n"
                         "Лучше всего PNG без фона. Если фон есть — уберу его автоматически. "
                         "Цвет не важен: его вы зададите в редакторе стиля.",
                   "en": "<b>Logo</b>\nSend your logo <b>as a file</b> (paperclip → File).\n\n"
                         "A transparent PNG works best. If it has a background, I'll remove it automatically. "
                         "Colour doesn't matter: you'll set it in the style editor."},
    "logo_bad":   {"ru": "Не смог открыть логотип. Пришлите PNG или JPG файлом (SVG пока не поддерживается).",
                   "en": "Couldn't open this logo. Send a PNG or JPG as a file (SVG isn't supported yet)."},
    "logo_empty": {"ru": "На картинке не нашёл логотип — он сливается с фоном. Пришлите версию с прозрачным фоном.",
                   "en": "Couldn't find a logo in this image — it blends into the background. Send a transparent version."},
    "logo_bg":    {"ru": "Фон убрал автоматически — проверьте на превью.",
                   "en": "Background removed automatically — check the preview."},
    "logo_photo": {"ru": "⚠️ Пришло как фото — Telegram сжал его и убрал прозрачность. Для лучшего качества пришлите файлом.",
                   "en": "⚠️ Sent as a photo — Telegram compressed it and dropped transparency. Send it as a file for best quality."},

    # ---------- Быстрый пост ----------
    "menu_hint":  {"ru": "📸 Чтобы сделать пост, пришлите фото — сразу покажу превью. "
                         "Первая строка подписи станет заголовком, #слово — хештегом.",
                   "en": "📸 To make a post, send photos — I'll show a preview right away. "
                         "The first line of the caption becomes the headline, a #word the hashtag."},
    "q_hint":     {"ru": "Чтобы сделать пост, пришлите фото. Подпись станет заголовком.",
                   "en": "To make a post, send photos. The caption becomes the headline."},
    "q_photos":   {"ru": "Фото: {n}", "en": "Photos: {n}"},
    "q_no_tag":   {"ru": "без хештега", "en": "no hashtag"},
    "q_no_title": {"ru": "Заголовка нет — нажмите «Текст» или пришлите фото с подписью.",
                   "en": "No headline — tap “Text” or send photos with a caption."},
    "b_q_text":   {"ru": "✏️ Текст", "en": "✏️ Text"},
    "b_q_send":   {"ru": "✅ Файлы ({n})", "en": "✅ Files ({n})"},
    "q_ask_text": {"ru": "✍️ Пришлите текст. Первая строка — заголовок, после пустой строки — подзаголовок, "
                         "#слово — хештег.",
                   "en": "✍️ Send the text. First line is the headline, after a blank line the subheadline, "
                         "a #word the hashtag."},
    "q_sending":  {"ru": "Готовлю файлы…", "en": "Preparing files…"},
    "no_access_short": {"ru": "Доступ закончился", "en": "Access has expired"},

    # ---------- Редактор (Mini App) ----------
    "k_setup":    {"ru": "🎨 Собрать бренд", "en": "🎨 Set up brand"},
    "b_desktop":  {"ru": "💻 На компьютере", "en": "💻 On a computer"},
    "b_open_desktop": {"ru": "Открыть редактор", "en": "Open the editor"},
    "desktop_link": {"ru": "Ссылка на редактор для компьютера. Откройте её в браузере на компьютере — "
                           "ссылка одноразовая и действует {min} минут. Вход сохранится на 7 дней.",
                     "en": "A link to the editor for your computer. Open it in a browser on your computer — "
                           "it works once and expires in {min} minutes. You'll stay signed in for 7 days."},
    "b_editor":   {"ru": "🎨 Редактор стиля", "en": "🎨 Style editor"},
    "wiz_done":   {"ru": "<i>Шаг 3 из 3</i>\n<b>Ваш стиль</b>\nОткройте редактор и соберите шаблоны: где стоит логотип, "
                         "какой шрифт у заголовка, нужна ли плашка, градиент или рамка. Для старта там уже есть два стиля — "
                         "меняйте их как угодно или начните с нуля.",
                   "en": "<i>Step 3 of 3</i>\n<b>Your style</b>\nOpen the editor and build your templates: where the logo sits, "
                         "which font the headline uses, whether you want a plate, gradient or frame. Two starter styles are "
                         "already there — change them freely or start from scratch."},
    "editor_off": {"ru": "Редактор пока не подключён — администратор должен задать WEBAPP_URL.",
                   "en": "The editor isn't connected yet — the admin needs to set WEBAPP_URL."},
    "no_tpl":     {"ru": "Шаблонов пока нет — соберите стиль в редакторе.",
                   "en": "No templates yet — build your style in the editor."},
    "tpl_story":  {"ru": " + сторис", "en": " + stories"},

    "kit_owner_only": {"ru": "Бренд-кит может менять только владелец бренда.",
                       "en": "Only the brand owner can edit the brand kit."},

    # ---------- Команда ----------
    "team_head": {"ru": "<b>Команда · {brand}</b>\nУчастников: {n} из {limit}\n\n"
                        "Отправьте эту ссылку коллегам — они смогут делать посты в вашем стиле:\n{link}",
                  "en": "<b>Team · {brand}</b>\nMembers: {n} of {limit}\n\n"
                        "Share this link with colleagues — they'll be able to make posts in your style:\n{link}"},
    "b_team_new": {"ru": "🔄 Новая ссылка (старая перестанет работать)",
                   "en": "🔄 New link (old one stops working)"},
    "team_owner_only": {"ru": "Приглашать в команду может только владелец бренда.",
                        "en": "Only the brand owner can invite team members."},

    # ---------- Создание поста ----------
    "no_access": {"ru": "Тариф неактивен — создание постов недоступно. Напишите {support}.",
                  "en": "Your plan is inactive — posting is unavailable. Contact {support}."},
    "no_logo":   {"ru": "Сначала загрузите логотип в бренд-ките.",
                  "en": "Upload a logo in your brand kit first."},
    "limit_hit": {"ru": "В этом месяце осталось {left} фото по тарифу, а в пакете {n}. Уберите лишние или напишите {support}.",
                  "en": "Your plan has {left} photos left this month, but the batch has {n}. Remove some or contact {support}."},
    "photos_max": {"ru": "Максимум {n} фото за раз — остальные не взял.",
                   "en": "Max {n} photos per batch — I skipped the rest."},
    "file_big":  {"ru": "Файл больше 20 МБ — это лимит Telegram для ботов. Пришлите файл поменьше.",
                  "en": "The file is over 20 MB — Telegram's limit for bots. Send a smaller file."},
    "photo_bad": {"ru": "Не смог открыть этот файл как изображение.",
                  "en": "Couldn't open this file as an image."},
    "fmt_orig":  {"ru": "Как в оригинале", "en": "Original ratio"},
    "tag_none":  {"ru": "— Без хештега —", "en": "— No hashtag —"},
    "tag_custom": {"ru": "✏️ Свой хештег", "en": "✏️ Custom hashtag"},
    "ask_custom_tag": {"ru": "Пришлите хештег одним словом — решётку добавлю сам.",
                       "en": "Send a one-word hashtag — I'll add the # myself."},
    "custom_tag_bad": {"ru": "Пустой хештег — пришлите ещё раз.", "en": "Empty hashtag — try again."},
    "photo_err": {"ru": "❌ Фото {i}: не получилось обработать.", "en": "❌ Photo {i}: processing failed."},
    "b_lighter": {"ru": "☀️ Светлее", "en": "☀️ Lighter"},
    "b_darker":  {"ru": "🌑 Темнее", "en": "🌑 Darker"},
    "edge":      {"ru": "Дальше некуда 🙂", "en": "That's the limit 🙂"},
    "stale":     {"ru": "Сессия устарела — откройте меню: /start", "en": "Session expired — open the menu: /start"},
    "cancelled": {"ru": "Отменено.", "en": "Cancelled."},
}


def t(lang: str, key: str, **kw) -> str:
    entry = T.get(key)
    if entry is None:
        return key
    s = entry.get(lang) or entry.get("ru") or key
    return s.format(**kw) if kw else s
