# -*- coding: utf-8 -*-
"""Methodology page (/ru/metodologiya/, /uk/metodolohiya/, /en/methodology/): a short, neutral, factual description of
what the pipeline in pipeline/*.py actually does. Static text, rendered at build time (no LLM). Placeholders filled by
build.py: {hold} (pipeline/cards.py HOLD_HOURS), {every} (hours between scheduled runs), {nsrc} (connected sources), {refresh} (ROUNDUP_REFRESH_HOURS),
{email} (contact address). Special blocks: ('sources',) = the source list from src/about.py SOURCES_TABLE, ('links',)."""

SLUG = {'ru': 'metodologiya', 'uk': 'metodolohiya', 'en': 'methodology'}

M = {}
M['ru'] = dict(
 nav='Методология', h1='Методология: как сайт собирает и сравнивает версии',
 title='Методология — как собираются и сравниваются версии СМИ России, Украины и независимых',
 desc='Как работает сайт: какие источники читаются, как статьи группируются в события, как проверяются цитаты и числа, когда ставится метка «другая сторона не комментирует» и как исправляются ошибки.',
 lede='Всё на сайте делается автоматически: сбор новостей, группировка в события, пересказы, переводы и публикация. Ниже — по шагам, что именно происходит и какие проверки выполняет код. Подробные таблицы источников, шкала подтверждённости и прогноз ошибок — на странице «О проекте».',
 toc='Содержание',
 sections=[
 ('sources', '1. Источники', [
  ('p', 'Сайт читает {nsrc} источников, разделённых на три группы. Группа обозначает принадлежность источника, а не его достоверность.'),
  ('sources',),
  ('p', 'Материалы берутся из открытых RSS-лент, карт сайта (sitemap), архивов разделов и публичных страниц статей. Учитывается robots.txt, запросы к каждому сайту идут с паузами, закрытые для автоматического чтения страницы не используются. Уже известные ссылки повторно не скачиваются. Плановый запуск — каждые {every} часа.'),
  ('p', 'Перед дальнейшей обработкой широкий список ключевых слов отбирает материалы, связанные с войной; остальные в карточки не попадают.'),
 ]),
 ('events', '2. Группировка в события', [
  ('p', 'Языковая модель получает пачки материалов вместе с названиями недавно открытых событий и относит каждый материал к существующему событию, к новому или ни к какому. Код проверяет ответ: убирает повторы, дозапрашивает пропущенные материалы и отбрасывает неизвестные идентификаторы. Отдельный проход ищет события, разбитые на части, и объединяет их.'),
  ('p', 'Затем для каждого материала отдельно задаётся вопрос «описывает ли он именно это событие?». В пересказ попадают только материалы с ответом «да», чтобы посторонняя статья не стала «версией» стороны.'),
 ]),
 ('summaries', '3. Пересказы по сторонам и проверка цитат', [
  ('p', 'Для каждой группы источников модель пишет короткий пересказ с указанием, кто что утверждает, блок «Где версии расходятся» и одну-две цитаты. Цитата публикуется, только если она дословно (с точностью до пробелов) встречается в сохранённом тексте статьи той же группы. Если у группы с пересказом не осталось ни одной проверенной цитаты, карточка не публикуется.'),
  ('p', 'Пересказы пишутся по-русски и машинно переводятся на украинский и английский. Код сверяет в переводе числа, язык, длину, названия населённых пунктов и фамилии; при расхождении делается один повторный перевод, затем карточка не публикуется. Цитаты не переводятся и приводятся на языке оригинала.'),
 ]),
 ('numbers', '4. Проверка чисел', [
  ('p', 'Каждое число в пересказе и в блоке расхождений должно встречаться в текстах источников. При несовпадении карточка пишется заново более сильной моделью; если несколько чисел не найдено, карточка не публикуется. Расхождение цифр между группами определяет код по числам, которые модель привела со ссылкой на конкретную статью и которые найдены в тексте этой статьи.'),
  ('p', 'Каждое событие получает метку подтверждённости («подтверждено независимыми источниками», «стороны противоречат друг другу», «заявляет одна сторона» и т. д.). Она считается в основном кодом по формальным признакам: сколько групп и разных источников описывают событие и прошли ли проверки. Метка описывает охват и согласованность материалов, а не истинность.'),
 ]),
 ('onesided', '5. Если пишет только одна сторона', [
  ('p', 'Новое событие, о котором сообщают источники только одной стороны войны (только российские или только украинские), ждёт до {hold} часов, не появится ли версия другой стороны. Независимые издания для этого правила стороной не считаются. Если за это время другая сторона так и не написала, карточка публикуется с пометкой «Другая сторона не комментирует». Это значит лишь, что в отслеживаемых источниках этой стороны сообщений о событии не найдено.'),
 ]),
 ('roundups', '6. Сводки дня', [
  ('p', 'Небольшие однотипные события, о которых не сообщают обе стороны, например атаки дронов на регионы России или удары по одной украинской области, не получают отдельных карточек. Код без участия модели собирает их в одну карточку «Сводка дня» на каждый день и тип. Пока день не закончился, сводка обновляется не чаще чем раз в {refresh} часов. Если событие из сводки становится значительнее (о нём пишет другая сторона, появляются данные о жертвах), оно получает собственную карточку.'),
 ]),
 ('dedup', '7. Повторы и обновления', [
  ('p', 'Раз в день код ищет пары опубликованных карточек, которые, похоже, описывают одно и то же: даты начала различаются не больше чем на день, регион тот же, слова в названии и месте похожи. Каждую пару подтверждает отдельный запрос к модели. Подтверждённые пары объединяются: материалы переходят в основную карточку, а адрес второй карточки перенаправляется (301) на основную.'),
  ('p', 'Когда о событии появляются новые материалы, карточка пересобирается и снова проходит все проверки. Если позже версия одной из сторон подтверждается или опровергается, на карточке появляется блок «Обновления» с датой и ссылкой на источник.'),
  ('p', 'Есть суточный лимит на обработку. Если его не хватает, события обрабатываются по важности (освещают обе стороны, есть независимые источники, есть данные о жертвах, речь о дипломатии или территории), а мелкие односторонние откладываются.'),
 ]),
 ('publishing', '8. Автоматическая публикация и исправления', [
  ('p', 'Карточки, пересказы и переводы создаёт языковая модель, а публикуются они автоматически, без проверки человеком. Карточка выходит, только если прошла все автоматические проверки; если опубликованная карточка перестаёт их проходить, например после прихода новых материалов, она снимается автоматически. Сайт не решает, какая версия верна, и не оценивает действия сторон.'),
  ('p', 'Ошибки возможны. Сообщить о них можно по ссылке «Сообщить об ошибке» на каждой карточке или письмом на {email}. Карточку перепроверяют и при подтверждении ошибки снимают или пересобирают. Все снятия и исправления, в том числе автоматические, записываются в публичный журнал исправлений и остаются в нём.'),
 ]),
 ('more', 'Подробнее', [('links',)]),
 ])

M['uk'] = dict(
 nav='Методологія', h1='Методологія: як сайт збирає та порівнює версії',
 title='Методологія — як збираються та порівнюються версії медіа Росії, України й незалежних',
 desc='Як працює сайт: які джерела читаються, як статті групуються в події, як перевіряються цитати й числа, коли ставиться позначка «інша сторона не коментує» і як виправляються помилки.',
 lede='Усе на сайті робиться автоматично: збір новин, групування в події, переказ, переклади й публікація. Нижче — крок за кроком, що саме відбувається і які перевірки виконує код. Докладні таблиці джерел, шкала підтвердженості та прогноз помилок — на сторінці «Про проєкт».',
 toc='Зміст',
 sections=[
 ('sources', '1. Джерела', [
  ('p', 'Сайт читає {nsrc} джерел, розділених на три групи. Група позначає належність джерела, а не його достовірність.'),
  ('sources',),
  ('p', 'Матеріали беруться з відкритих RSS-стрічок, карт сайту (sitemap), архівів розділів і публічних сторінок статей. Враховується robots.txt, запити до кожного сайту йдуть із паузами, закриті для автоматичного читання сторінки не використовуються. Уже відомі посилання повторно не завантажуються. Плановий запуск — кожні {every} години.'),
  ('p', 'Перед подальшою обробкою широкий список ключових слів відбирає матеріали, пов’язані з війною; решта до карток не потрапляє.'),
 ]),
 ('events', '2. Групування в події', [
  ('p', 'Мовна модель отримує пачки матеріалів разом із назвами нещодавно відкритих подій і відносить кожен матеріал до наявної події, до нової або до жодної. Код перевіряє відповідь: прибирає повтори, дозапитує пропущені матеріали й відкидає невідомі ідентифікатори. Окремий прохід шукає події, розбиті на частини, і об’єднує їх.'),
  ('p', 'Потім для кожного матеріалу окремо ставиться питання «чи описує він саме цю подію?». До переказу потрапляють лише матеріали з відповіддю «так», щоб стороння стаття не стала «версією» сторони.'),
 ]),
 ('summaries', '3. Перекази за сторонами та перевірка цитат', [
  ('p', 'Для кожної групи джерел модель пише короткий переказ із зазначенням, хто що стверджує, блок «Де версії розходяться» та одну-дві цитати. Цитата публікується, лише якщо вона дослівно (з точністю до пробілів) трапляється в збереженому тексті статті тієї ж групи. Якщо в групи з переказом не лишилося жодної перевіреної цитати, картка не публікується.'),
  ('p', 'Перекази пишуться російською й машинно перекладаються українською та англійською. Код звіряє в перекладі числа, мову, довжину, назви населених пунктів і прізвища; у разі розбіжності робиться один повторний переклад, далі картка не публікується. Цитати не перекладаються й наводяться мовою оригіналу.'),
 ]),
 ('numbers', '4. Перевірка чисел', [
  ('p', 'Кожне число в переказі та в блоці розбіжностей має траплятися в текстах джерел. У разі невідповідності картка пишеться заново сильнішою моделлю; якщо кілька чисел не знайдено, картка не публікується. Розбіжність цифр між групами визначає код за числами, які модель навела з посиланням на конкретну статтю і які знайдено в тексті цієї статті.'),
  ('p', 'Кожна подія отримує позначку підтвердженості («підтверджено незалежними джерелами», «сторони суперечать одна одній», «заявляє одна сторона» тощо). Її обчислює переважно код за формальними ознаками: скільки груп і різних джерел описують подію та чи пройдено перевірки. Позначка описує охоплення й узгодженість матеріалів, а не істинність.'),
 ]),
 ('onesided', '5. Якщо пише лише одна сторона', [
  ('p', 'Нова подія, про яку повідомляють джерела лише однієї сторони війни (лише російські або лише українські), чекає до {hold} годин, чи не з’явиться версія іншої сторони. Незалежні видання для цього правила стороною не вважаються. Якщо за цей час інша сторона так і не написала, картка публікується з позначкою «Інша сторона не коментує». Це означає лише, що у відстежуваних джерелах цієї сторони повідомлень про подію не знайдено.'),
 ]),
 ('roundups', '6. Зведення дня', [
  ('p', 'Невеликі однотипні події, про які не повідомляють обидві сторони, наприклад атаки дронів на регіони Росії або удари по одній українській області, не отримують окремих карток. Код без участі моделі збирає їх в одну картку «Зведення дня» на кожен день і тип. Поки день не скінчився, зведення оновлюється не частіше ніж раз на {refresh} годин. Якщо подія зі зведення стає вагомішою (про неї пише інша сторона, з’являються дані про жертви), вона отримує власну картку.'),
 ]),
 ('dedup', '7. Повтори та оновлення', [
  ('p', 'Раз на день код шукає пари опублікованих карток, які, схоже, описують те саме: дати початку різняться не більше ніж на день, регіон той самий, слова в назві й місці схожі. Кожну пару підтверджує окремий запит до моделі. Підтверджені пари об’єднуються: матеріали переходять до основної картки, а адреса другої картки переспрямовується (301) на основну.'),
  ('p', 'Коли про подію з’являються нові матеріали, картка перезбирається й знову проходить усі перевірки. Якщо згодом версія однієї зі сторін підтверджується або спростовується, на картці з’являється блок «Оновлення» з датою та посиланням на джерело.'),
  ('p', 'Є добовий ліміт на обробку. Якщо його не вистачає, події обробляються за важливістю (висвітлюють обидві сторони, є незалежні джерела, є дані про жертви, ідеться про дипломатію чи територію), а дрібні односторонні відкладаються.'),
 ]),
 ('publishing', '8. Автоматична публікація та виправлення', [
  ('p', 'Картки, перекази й переклади створює мовна модель, а публікуються вони автоматично, без перевірки людиною. Картка виходить, лише якщо пройшла всі автоматичні перевірки; якщо опублікована картка перестає їх проходити, наприклад після надходження нових матеріалів, її знімають автоматично. Сайт не вирішує, яка версія правильна, і не оцінює дій сторін.'),
  ('p', 'Помилки можливі. Повідомити про них можна за посиланням «Повідомити про помилку» на кожній картці або листом на {email}. Картку перевіряють повторно й у разі підтвердження помилки знімають або перезбирають. Усі зняття й виправлення, зокрема автоматичні, записуються до публічного журналу виправлень і залишаються в ньому.'),
 ]),
 ('more', 'Докладніше', [('links',)]),
 ])

M['en'] = dict(
 nav='Methodology', h1='Methodology: how the site collects and compares versions',
 title='Methodology — how Russian, Ukrainian and independent media versions are compared',
 desc='How the site works: which sources are read, how articles are grouped into events, how quotes and numbers are checked, when the “other side has not commented” label is used and how errors are corrected.',
 lede='Everything on this site is automated: collecting news, grouping it into events, summaries, translations and publishing. Below, step by step, is what happens and which checks the code runs. Detailed source tables, the confirmation scale and the error forecast are on the About page.',
 toc='Contents',
 sections=[
 ('sources', '1. Sources', [
  ('p', 'The site reads {nsrc} sources in three groups. A group labels a source’s affiliation, not its reliability.'),
  ('sources',),
  ('p', 'Articles come from public RSS feeds, sitemaps, section archives and public article pages. robots.txt is honoured, requests to each site are spaced out, and pages closed to automated reading are not used. Links that are already known are not downloaded again. A scheduled run starts every {every} hours.'),
  ('p', 'Before further processing, a broad keyword list selects war-related items; everything else never reaches the cards.'),
 ]),
 ('events', '2. Grouping into events', [
  ('p', 'A language model receives batches of articles together with the titles of recently opened events and assigns each article to an existing event, a new one, or none. Code validates the answer: it removes duplicates, asks again about missing articles and discards unknown ids. A separate pass looks for events that were split into parts and merges them.'),
  ('p', 'Then, for every article separately, the question “does it describe this particular event?” is asked. Only articles answered “yes” go into a summary, so an unrelated story cannot become one side’s “version”.'),
 ]),
 ('summaries', '3. Per-side summaries and the quote check', [
  ('p', 'For each source group the model writes a short summary that attributes every claim, a “Where the versions diverge” block, and one or two quotes. A quote is published only if it appears verbatim (up to whitespace) in the stored text of an article from the same group. If a group with a summary has no verified quote left, the card is not published.'),
  ('p', 'Summaries are written in Russian and machine-translated into Ukrainian and English. Code compares numbers, language, length, place names and surnames in the translation; on a mismatch there is one retranslation, after which the card is not published. Quotes are not translated and stay in the original language.'),
 ]),
 ('numbers', '4. The number check', [
  ('p', 'Every number in a summary and in the divergence block must occur in the source texts. On a mismatch the card is rewritten by a stronger model; if several numbers are not found, the card is not published. A figure conflict between groups is determined by code, from numbers the model cited with a specific article that were found in that article’s text.'),
  ('p', 'Each event gets a confirmation label (“confirmed by independent sources”, “the sides contradict each other”, “claimed by one side only” and so on). It is computed mostly by code from formal features: how many groups and distinct sources describe the event and whether the checks passed. The label describes coverage and consistency of the material, not truth.'),
 ]),
 ('onesided', '5. When only one side reports', [
  ('p', 'A new event reported only by sources of one side of the war (only Russian or only Ukrainian) waits up to {hold} hours for the other side’s version. Independent outlets do not count as a side for this rule. If the other side still has not reported it by then, the card is published with the label “The other side has not commented”. This only means that no reports of the event were found in the sources tracked for that side.'),
 ]),
 ('roundups', '6. Daily roundups', [
  ('p', 'Minor events of the same kind that are not reported by both sides, such as drone attacks on Russian regions or strikes on a single Ukrainian region, do not get separate cards. Code, without the model, groups them into one “Daily roundup” card per day and kind. While the day is open, a roundup is refreshed at most every {refresh} hours. If an event in a roundup becomes more significant (the other side reports it, casualty figures appear), it gets its own card.'),
 ]),
 ('dedup', '7. Duplicates and updates', [
  ('p', 'Once a day, code looks for pairs of published cards that seem to describe the same thing: start dates at most one day apart, same region, similar words in title and place. Each pair is confirmed by a separate model request. Confirmed pairs are merged: the articles move to the main card and the other card’s address is redirected (301) to it.'),
  ('p', 'When new articles about an event arrive, the card is rebuilt and goes through all checks again. If one side’s version is later confirmed or refuted, the card gets an “Updates” block with the date and a link to the source.'),
  ('p', 'Processing has a daily limit. When it is not enough, events are handled in order of importance (covered by both sides, independent sources present, casualty figures, diplomacy or territory), and minor one-sided ones are postponed.'),
 ]),
 ('publishing', '8. Automatic publishing and corrections', [
  ('p', 'Cards, summaries and translations are produced by a language model and published automatically, with no human review. A card goes live only if it passes all automatic checks; if a published card stops passing them, for example after new articles arrive, it is withdrawn automatically. The site does not decide which version is true and does not judge the parties’ actions.'),
  ('p', 'Errors are possible. You can report one with the “Report an error” link on every card or by e-mail to {email}. The card is re-checked and, if the error is confirmed, withdrawn or rebuilt. All withdrawals and corrections, including automatic ones, are recorded in the public corrections log and stay there.'),
 ]),
 ('more', 'More', [('links',)]),
 ])
