# -*- coding: utf-8 -*-
"""About page content (methodology, confidence scale, error forecast, corrections policy, neutrality, anonymity, source limitations) in ru / uk / en.
Numbers quoted in the error forecast come from /workspace/llm-test/REPORT.md (one test run on 2026-10-04, 8-9 events) and are small-sample estimates.
Sections are lists of blocks: ('p', text) | ('ul', [items]) | ('labels',) | ('sources',) | ('blocked',)"""

ABOUT = {}

ABOUT['ru'] = dict(
 intro='Сайт сравнивает, как российские, украинские и независимые СМИ освещают одни и те же события войны в Украине. Новые карточки событий, пересказы и переводы создаёт и публикует автоматически языковая модель Qwen (Alibaba Cloud), без ручной проверки перед публикацией; результат проходит автоматические проверки в коде (дословность цитат, наличие каждого числа в источниках, совпадение чисел и географических названий в переводах). Более ранние карточки частично подготовлены вручную или другими языковыми моделями. Сайт не устанавливает истину: он показывает, что и как сообщает каждая группа источников, и где версии расходятся.',
 toc='Содержание',
 sections=[
 ('method', 'Методика', [
  ('p', 'Один автоматический конвейер (скрипт update.py) выполняет шаги ниже. Каждый шаг, где возможно, дополнен проверкой в коде — модель не «проверяет сама себя».'),
  ('ul', [
   'Сбор. Скрипт читает публичные страницы источников (RSS, sitemap, дневные архивы, страницы статей) и сохраняет заголовок, ссылку, время, группу и текст (до 6000 знаков) в локальную базу. Сбор инкрементальный: уже известные адреса не загружаются повторно. К каждому сайту — пауза между запросами, соблюдается robots.txt; страницы, закрытые для автоматического чтения, не используются.',
   'Предварительный отбор. По ключевым словам (широкий список) отбираются материалы, относящиеся к войне; остальные в карточки не попадают.',
   'Группировка в события. Языковая модель (Qwen от Alibaba Cloud, qwen3.7-plus) получает партии до 100 материалов вместе с названиями недавно открытых событий и относит материалы к существующим событиям, создаёт новые либо оставляет материал без события. Код проверяет ответ: убирает повторы, добавляет пропущенные номера (отдельный запрос), отбрасывает несуществующие. Затем отдельный проход ищет события, разбитые на части, и объединяет их.',
   'Фильтр релевантности. Для каждого материала отдельно задаётся вопрос «описывает ли он именно это событие?». В пересказ попадают только материалы с ответом «да». Так посторонний материал не становится «версией» одной из сторон.',
   'Пересказы. Для каждой из трёх групп (Россия, Украина, независимые) модель пишет краткий пересказ с атрибуцией («Минобороны РФ заявляет…»), блок «Где версии расходятся» и 1–2 цитаты на группу. События с числовыми расхождениями (по заранее проверяемым цифрам потерь) или с большим числом материалов сразу отправляются более сильной модели (qwen3.7-plus); остальные — сначала qwen3.7-flash, а при неудачной проверке — один повтор на qwen3.7-plus.',
   'Проверка цитат. Каждая цитата должна быть дословной подстрокой сохранённого текста статьи из той же группы (допускается только исправление пробелов). Цитата, не прошедшая проверку, не публикуется. Если у группы с пересказом не осталось ни одной проверенной цитаты, карточка не публикуется.',
   'Проверка чисел. Каждое число в пересказе и в блоке расхождений должно встречаться в текстах источников. Несовпадение — повод для повтора на более сильной модели; при нескольких несовпадениях карточка не публикуется. Расхождение цифр между группами определяется в коде по числам, которые модель привела со ссылкой на статью и которые код нашёл в тексте этой статьи.',
   'Переводы. Заголовок, место, пересказы и блок расхождений пишутся по-русски и затем машинно переводятся на украинский и английский (qwen3.7-plus). Код проверяет, что числа в переводе совпадают, письменность и длина правдоподобны, а названия населённых пунктов, областей и фамилии в переводе соответствуют оригиналу (для английского — по справочнику GeoNames, CC BY 4.0; при несовпадении — один повторный перевод с подсказкой, затем перевод не публикуется); цитаты не переводятся. Не прошедшая проверку карточка не публикуется.',
   'Публикация. Карточка выходит на сайт автоматически, если прошла все «ворота качества» (ниже). Ручной проверки нет.'
  ]),
  ('p', 'Ворота качества: карточка не публикуется, если (1) меньше двух релевантных материалов или меньше двух разных источников; (2) фильтр релевантности подтвердил менее половины сгруппированных материалов; (3) у группы с пересказом нет проверенной цитаты; (4) в пересказе несколько чисел не найдены в источниках; (5) модель сообщила о высокой неуверенности; (6) не прошла проверка перевода; (7) ответ модели неполон или некорректен. Если ранее опубликованная карточка перестала проходить эти проверки (например, после появления новых материалов), она снимается автоматически, а запись об этом попадает в журнал исправлений.'),
 ]),
 ('conf', 'Шкала подтверждённости', [
  ('p', 'Для каждого события считается показатель подтверждённости — в основном по формальным признакам, в коде: сколько групп и разных источников описывают событие; есть ли среди них международные или эмигрантские независимые издания (Kyiv Independent относится к украинским изданиям и для этой цели не считается международным); расходятся ли числа (различие чисел само по себе не даёт метку «противоречат»: «запущено» и «сбито» — разные величины; метка ставится, только если модель по тексту отметила несовместимые утверждения); прошла ли проверка цитат и чисел; насколько фильтр релевантности подтвердил группировку; насколько неуверенной сочла карточку сама модель. Показатель отображается значком и текстом (не только цветом).'),
  ('labels',),
  ('p', 'Важно: метка описывает охват и согласованность материалов в собранных данных, а не истинность. «Подтверждено независимыми источниками» означает, что событие описывают независимые издания, а не то, что они проверили каждую деталь; они могут пересказывать заявление одной из сторон. Метка выставляется автоматически и может быть неверной.'),
 ]),
 ('errors', 'Чего ожидать: прогноз ошибок', [
  ('p', 'Ниже — виды ошибок, которых следует ожидать, и грубые оценки из тестовых прогонов: от 2026-10-04 (модели OpenAI, которыми готовились более ранние карточки) и от ноября 2026 г. (Qwen). Выборки малы (в тесте группировки — 100 материалов и 9 эталонных карточек; в тесте пересказов — 8 карточек), поэтому цифры ориентировочные и не переносятся на весь сайт.'),
  ('ul', [
   'Разбитые и склеенные события. В тесте группировки (gpt-6-luna, режим по умолчанию) в среднем 63% ключевых материалов эталонной карточки попадало в один лучший кластер; у 3 из 9 карточек — не менее 75%. Модель осторожна: из 100 материалов 55–69 остаются без события, а одно событие нередко делится на две карточки. Из 17 кластеров 2 были смешанными (смежные эпизоды); оба случая спорные. Эталон сам неточен, так что оценка приблизительная.',
   'Неверная принадлежность материала. Без фильтра релевантности в одной из 8 проверенных карточек черновик выдал постороннюю статью (о другом сюжете) за «российскую версию» события. Именно поэтому введён фильтр; его собственная точность отдельно не измерялась.',
   'Поверхностный блок «Где версии расходятся». Это самое изменчивое поле: у более слабых режимов модели он нередко сводился к общим словам вместо конкретных цифр и формулировок; сильная модель описывала расхождения точнее. Блок только фиксирует различия, он не проверяет факты.',
   'Цитаты и числа. В тесте Qwen (ноябрь 2026 г.) 94% цитат оказались дословными с первой попытки, остальные модель исправила при повторе; цитаты, не прошедшие проверку, не публикуются. В более раннем тесте gpt-6-luna (режим по умолчанию) все 35 цитат из 35 оказались дословными; у более слабого режима 40 из 42 (он в конвейере не используется). Среди 25 чисел в пересказах одно не нашлось в источнике (дата). Выборка мала, а проверенные вручную ~15 утверждений вымышленных фактов не содержали. Дословная цитата не гарантирует, что она хорошо выбрана или репрезентативна.',
   'Ошибки перевода. На украинский и английский переводятся пересказы и блок расхождений. Доля ошибок перевода в тесте не измерялась; в одном образце сильной модели в русский текст попало английское слово, в тесте Qwen в английском переводе один город был заменён другим (поэтому добавлена проверка географических названий). Код отсекает грубые сбои (числа, язык, длина, названия мест и фамилии), но не смысловые неточности.',
   'Пропуски и смещение выборки. «Материалов не найдено» означает отсутствие в собранных данных, а не в СМИ вообще. Набор событий определяется автоматической группировкой и доступностью источников.'
  ]),
 ]),
 ('corrections', 'Политика исправлений', [
  ('ul', [
   'Как сообщить об ошибке: ссылка «Сообщить об ошибке» на каждой карточке и в подвале сайта (письмо по электронной почте; в письмо подставляется адрес страницы). Адрес сейчас — заглушка, пока не настроен.',
   'Что происходит с сообщением: карточку перепроверяют (повторно запускают проверки по сохранённым текстам); если ошибка подтверждается, карточка снимается с публикации, а при необходимости пересоздаётся. Сроков ответа проект не обещает.',
   'Журнал исправлений: все снятия и исправления фиксируются на публичной странице «Исправления» — дата, карточка, что произошло и почему. Автоматические снятия (карточка перестала проходить проверки) записываются туда же и помечены как автоматические.',
   'Исправление не стирает историю: запись остаётся в журнале и после того, как карточка снята или заменена.'
  ]),
 ]),
 ('neutrality', 'Нейтральность', [
  ('ul', [
   'Утверждения приписываются тем, кто их высказал («Минобороны РФ заявляет…», «по данным ОВА…», «BBC пишет…»); спорные утверждения не подаются как факты.',
   'Сайт не определяет, какая из версий верна, и не оценивает действия сторон. Блок «Где версии расходятся» фиксирует различия в цифрах и формулировках.',
   'Деление на группы — это пометка принадлежности источника, а не оценка достоверности: «Россия» — российские государственные и государственно-лояльные издания; «Украина» — украинские издания (публичный вещатель и частные); «Независимые» — международные и эмигрантские издания, а также Kyiv Independent. Часть независимых изданий получает государственное или общественное финансирование (BBC, DW, Al Jazeera); редакционная независимость — их собственное заявление, мы её не проверяем.',
   'Выбор источников сам по себе создаёт смещение: в российской группе нет изданий, критичных к власти и доступных для автоматического чтения, а Kyiv Independent может занимать проукраинскую позицию.'
  ]),
 ]),
 ('anon', 'Анонимность проекта', [
  ('p', 'Проект анонимен: на сайте и в коде нет имён и личных контактов авторов. Единственный контакт — адрес для обратной связи (сейчас заглушка). Сайт не использует счётчики и cookies; в браузере локально запоминаются только выбранные язык и тема оформления.'),
 ]),
 ('sources', 'Источники и их ограничения', [
  ('p', 'Список ниже — все источники, которые использует конвейер, с описанием принадлежности и способа доступа. Число материалов — количество сохранённых статей в базе (включая не относящиеся к событиям).'),
  ('sources',),
  ('p', 'Источники, проверенные, но не используемые или доступные лишь частично:'),
  ('blocked',),
  ('p', 'Ограничения:'),
  ('ul', [
   'Глубина сбора ограничена тем, что отдают RSS, карты сайта и архивы: у RIA, BBC, France 24, Meduza, Guardian, DW, Interfax и др. часть материалов за прошлые дни недоступна.',
   'У ТАСС на русском собираются только заголовки и лиды из RSS; у Минобороны РФ — тексты постов Telegram-канала (публичное веб-превью).',
   'Российская группа состоит из государственных и государственно-лояльных источников; украинская — из публичного вещателя и частных изданий, а Kyiv Independent относится к «независимым» условно. «Независимость» — приблизительное понятие.',
   'Независимой проверки фактов в проекте нет.'
  ]),
 ]),
 ('contact', 'Контакты', [('contact',)]),
 ])

ABOUT['uk'] = dict(
 intro='Сайт порівнює, як російські, українські та незалежні медіа висвітлюють одні й ті самі події війни в Україні. Нові картки подій, перекази та переклади автоматично створює і публікує мовна модель Qwen (Alibaba Cloud), без ручної перевірки перед публікацією; результат проходить автоматичні перевірки в коді (дослівність цитат, наявність кожного числа в джерелах, збіг чисел і географічних назв у перекладах). Раніші картки частково підготовлено вручну або іншими мовними моделями. Сайт не встановлює істину: він показує, що і як повідомляє кожна група джерел та де версії розходяться.',
 toc='Зміст',
 sections=[
 ('method', 'Методика', [
  ('p', 'Один автоматичний конвеєр (скрипт update.py) виконує кроки нижче. Де можливо, кожен крок доповнено перевіркою в коді — модель не «перевіряє сама себе».'),
  ('ul', [
   'Збір. Скрипт читає публічні сторінки джерел (RSS, sitemap, денні архіви, сторінки статей) і зберігає заголовок, посилання, час, групу та текст (до 6000 знаків) у локальну базу. Збір інкрементальний: уже відомі адреси повторно не завантажуються. До кожного сайту — пауза між запитами, дотримується robots.txt; сторінки, закриті для автоматичного читання, не використовуються.',
   'Попередній відбір. За ключовими словами (широкий перелік) відбираються матеріали, пов’язані з війною; решта до карток не потрапляє.',
   'Групування в події. Мовна модель (Qwen від Alibaba Cloud, qwen3.7-plus) отримує партії до 100 матеріалів разом із назвами нещодавно відкритих подій і відносить матеріали до наявних подій, створює нові або залишає матеріал без події. Код перевіряє відповідь: прибирає повтори, додає пропущені номери (окремий запит), відкидає неіснуючі. Потім окремий прохід шукає події, розбиті на частини, і об’єднує їх.',
   'Фільтр релевантності. Для кожного матеріалу окремо ставиться питання «чи описує він саме цю подію?». До переказу потрапляють лише матеріали з відповіддю «так». Так сторонній матеріал не стає «версією» однієї зі сторін.',
   'Перекази. Для кожної з трьох груп (Росія, Україна, незалежні) модель пише короткий переказ з атрибуцією («Міноборони РФ заявляє…»), блок «Де версії розходяться» та 1–2 цитати на групу. Події з числовими розбіжностями (за наперед перевірюваними цифрами втрат) або з великою кількістю матеріалів одразу йдуть до потужнішої моделі (qwen3.7-plus); решта — спершу qwen3.7-flash, а за невдалої перевірки — один повтор на qwen3.7-plus.',
   'Перевірка цитат. Кожна цитата має бути дослівним фрагментом збереженого тексту статті з тієї ж групи (дозволено лише виправлення пробілів). Цитата, що не пройшла перевірку, не публікується. Якщо в групи з переказом не залишилося жодної перевіреної цитати, картка не публікується.',
   'Перевірка чисел. Кожне число в переказі та в блоці розбіжностей має зустрічатися в текстах джерел. Невідповідність — привід для повтору на потужнішій моделі; за кількох невідповідностей картка не публікується. Розбіжність цифр між групами визначається в коді за числами, які модель навела з посиланням на статтю і які код знайшов у тексті цієї статті.',
   'Переклади. Заголовок, місце, перекази та блок розбіжностей пишуться російською, а потім машинно перекладаються українською та англійською (qwen3.7-plus). Код перевіряє, що числа в перекладі збігаються, писемність і довжина правдоподібні, а назви населених пунктів, областей і прізвища в перекладі відповідають оригіналу (для англійської — за довідником GeoNames, CC BY 4.0; за розбіжності — один повторний переклад із підказкою, далі переклад не публікується); цитати не перекладаються. Картка, що не пройшла перевірку, не публікується.',
   'Публікація. Картка виходить на сайт автоматично, якщо пройшла всі «ворота якості» (нижче). Ручної перевірки немає.'
  ]),
  ('p', 'Ворота якості: картка не публікується, якщо (1) менше двох релевантних матеріалів або менше двох різних джерел; (2) фільтр релевантності підтвердив менше половини згрупованих матеріалів; (3) у групи з переказом немає перевіреної цитати; (4) у переказі кілька чисел не знайдено в джерелах; (5) модель повідомила про високу невпевненість; (6) не пройшла перевірка перекладу; (7) відповідь моделі неповна або некоректна. Якщо раніше опублікована картка перестала проходити ці перевірки (наприклад, після появи нових матеріалів), її знімають автоматично, а запис про це потрапляє до журналу виправлень.'),
 ]),
 ('conf', 'Шкала підтвердженості', [
  ('p', 'Для кожної події обчислюється показник підтвердженості — переважно за формальними ознаками, у коді: скільки груп і різних джерел описують подію; чи є серед них міжнародні або емігрантські незалежні видання (Kyiv Independent належить до українських видань і для цього не вважається міжнародним); чи розходяться числа (відмінність чисел сама по собі не дає позначки «суперечать»: «запущено» і «збито» — різні величини; позначка ставиться, лише якщо модель за текстом відзначила несумісні твердження); чи пройшла перевірка цитат і чисел; наскільки фільтр релевантності підтвердив групування; наскільки невпевненою вважала картку сама модель. Показник відображається значком і текстом (не лише кольором).'),
  ('labels',),
  ('p', 'Важливо: мітка описує охоплення та узгодженість матеріалів у зібраних даних, а не істинність. «Підтверджено незалежними джерелами» означає, що подію описують незалежні видання, а не що вони перевірили кожну деталь; вони можуть переказувати заяву однієї зі сторін. Мітка виставляється автоматично і може бути хибною.'),
 ]),
 ('errors', 'Чого очікувати: прогноз помилок', [
  ('p', 'Нижче — види помилок, яких слід очікувати, та грубі оцінки з тестових прогонів: від 2026-10-04 (моделі OpenAI, якими готувалися раніші картки) і з листопада 2026 р. (Qwen). Вибірки малі (у тесті групування — 100 матеріалів і 9 еталонних карток; у тесті переказів — 8 карток), тому цифри орієнтовні й не переносяться на весь сайт.'),
  ('ul', [
   'Розбиті та склеєні події. У тесті групування (gpt-6-luna, режим за замовчуванням) у середньому 63% ключових матеріалів еталонної картки потрапляло в один найкращий кластер; у 3 з 9 карток — не менше 75%. Модель обережна: зі 100 матеріалів 55–69 залишаються без події, а одна подія нерідко ділиться на дві картки. Із 17 кластерів 2 були змішаними (суміжні епізоди); обидва випадки спірні. Еталон і сам неточний, тож оцінка приблизна.',
   'Хибна приналежність матеріалу. Без фільтра релевантності в одній із 8 перевірених карток чернетка видала сторонню статтю (про інший сюжет) за «російську версію» події. Саме тому введено фільтр; його власну точність окремо не вимірювали.',
   'Поверхневий блок «Де версії розходяться». Це найменш стабільне поле: у слабших режимів моделі він нерідко зводився до загальних слів замість конкретних цифр і формулювань; потужніша модель описувала розбіжності точніше. Блок лише фіксує відмінності, він не перевіряє факти.',
   'Цитати та числа. У тесті Qwen (листопад 2026 р.) 94% цитат виявилися дослівними з першої спроби, решту модель виправила при повторі; цитати, що не пройшли перевірку, не публікуються. У ранішому тесті gpt-6-luna (режим за замовчуванням) усі 35 цитат із 35 виявилися дослівними; у слабшого режиму 40 із 42 (у конвеєрі він не використовується). Серед 25 чисел у переказах одне не знайшлося в джерелі (дата). Вибірка мала, а вручну перевірені ~15 тверджень вигаданих фактів не містили. Дослівна цитата не гарантує, що її обрано вдало чи репрезентативно.',
   'Помилки перекладу. Українською та англійською перекладаються перекази й блок розбіжностей. Частку помилок перекладу в тесті не вимірювали; в одному зразку потужнішої моделі в російський текст потрапило англійське слово, а в тесті Qwen в англійському перекладі одне місто замінено іншим (тому додано перевірку географічних назв). Код відсікає грубі збої (числа, мова, довжина, назви місць і прізвища), але не смислові неточності.',
   'Пропуски та зсув вибірки. «Матеріалів не знайдено» означає відсутність у зібраних даних, а не в медіа взагалі. Набір подій визначається автоматичним групуванням і доступністю джерел.'
  ]),
 ]),
 ('corrections', 'Політика виправлень', [
  ('ul', [
   'Як повідомити про помилку: посилання «Повідомити про помилку» на кожній картці та в підвалі сайту (лист електронною поштою; у лист підставляється адреса сторінки). Адреса зараз — заглушка, поки не налаштована.',
   'Що відбувається зі повідомленням: картку перевіряють повторно (знову запускають перевірки за збереженими текстами); якщо помилку підтверджено, картку знімають з публікації, а за потреби створюють заново. Строків відповіді проєкт не обіцяє.',
   'Журнал виправлень: усі зняття й виправлення фіксуються на публічній сторінці «Виправлення» — дата, картка, що сталося і чому. Автоматичні зняття (картка перестала проходити перевірки) записуються туди ж і позначені як автоматичні.',
   'Виправлення не стирає історію: запис лишається в журналі й після того, як картку знято або замінено.'
  ]),
 ]),
 ('neutrality', 'Нейтральність', [
  ('ul', [
   'Твердження приписуються тим, хто їх висловив («Міноборони РФ заявляє…», «за даними ОВА…», «BBC пише…»); спірні твердження не подаються як факти.',
   'Сайт не визначає, яка з версій правдива, і не оцінює дій сторін. Блок «Де версії розходяться» фіксує відмінності в цифрах і формулюваннях.',
   'Поділ на групи — це позначка належності джерела, а не оцінка достовірності: «Росія» — російські державні й державно-лояльні видання; «Україна» — українські видання (публічний мовник і приватні); «Незалежні» — міжнародні та емігрантські видання, а також Kyiv Independent. Частина незалежних видань отримує державне або суспільне фінансування (BBC, DW, Al Jazeera); редакційна незалежність — їхня власна заява, ми її не перевіряємо.',
   'Вибір джерел сам по собі створює зсув: у російській групі немає видань, критичних до влади й доступних для автоматичного читання, а Kyiv Independent може займати проукраїнську позицію.'
  ]),
 ]),
 ('anon', 'Анонімність проєкту', [
  ('p', 'Проєкт анонімний: на сайті й у коді немає імен та особистих контактів авторів. Єдиний контакт — адреса для зворотного зв’язку (зараз заглушка). Сайт не використовує лічильники й cookies; у браузері локально запам’ятовуються лише обрані мова й тема оформлення.'),
 ]),
 ('sources', 'Джерела та їхні обмеження', [
  ('p', 'Перелік нижче — усі джерела, які використовує конвеєр, з описом належності та способу доступу. Кількість матеріалів — кількість збережених статей у базі (включно з тими, що не стосуються подій).'),
  ('sources',),
  ('p', 'Джерела, перевірені, але не використовувані або доступні лише частково:'),
  ('blocked',),
  ('p', 'Обмеження:'),
  ('ul', [
   'Глибина збору обмежена тим, що віддають RSS, мапи сайту й архіви: у RIA, BBC, France 24, Meduza, Guardian, DW, Interfax та ін. частина матеріалів за минулі дні недоступна.',
   'У ТАСС російською збираються лише заголовки й ліди з RSS; у Міноборони РФ — тексти дописів Telegram-каналу (публічне веб-превʼю).',
   'Російська група складається з державних і державно-лояльних джерел; українська — з публічного мовника та приватних видань, а Kyiv Independent належить до «незалежних» умовно. «Незалежність» — приблизне поняття.',
   'Незалежної перевірки фактів у проєкті немає.'
  ]),
 ]),
 ('contact', 'Контакти', [('contact',)]),
 ])

ABOUT['en'] = dict(
 intro='This site compares how Russian, Ukrainian and independent media cover the same events of the war in Ukraine. New event cards, summaries and translations are created and published automatically by a language model, Qwen (Alibaba Cloud), with no human review before publication; the output goes through automatic checks in code (verbatim quotes, every number present in the sources, matching numbers and place names in translations). Earlier cards were partly prepared by hand or by other language models. The site does not establish the truth: it shows what each group of sources reports, how, and where the versions diverge.',
 toc='Contents',
 sections=[
 ('method', 'Methodology', [
  ('p', 'One automatic pipeline (the update.py script) runs the steps below. Wherever possible each step is backed by a check in code — the model does not “check itself”.'),
  ('ul', [
   'Collection. The script reads public pages of the sources (RSS, sitemaps, daily archives, article pages) and stores title, link, time, group and text (up to 6,000 characters) in a local database. Collection is incremental: URLs already known are not downloaded again. Requests to each site are rate-limited and robots.txt is honoured; pages closed to automated reading are not used.',
   'Pre-selection. A broad keyword list selects war-related items; everything else never reaches the cards.',
   'Clustering into events. A language model (Qwen by Alibaba Cloud, qwen3.7-plus) receives batches of up to 100 items together with the titles of recently opened events, and attaches items to existing events, creates new ones, or leaves an item without an event. Code validates the answer: removes duplicate ids, re-asks for missing ids (a separate request) and discards unknown ones. A separate pass then looks for events that were split into parts and merges them.',
   'Relevance filter. For every item the question “does it actually describe this event?” is asked separately. Only items answered “yes” enter a summary, so an unrelated item never becomes a side’s “version”.',
   'Summaries. For each of the three groups (Russia, Ukraine, independent) the model writes a short summary with attribution (“the Russian Defense Ministry says…”), a “Where the versions diverge” block, and 1–2 quotes per group. Events with numeric discrepancies (casualty-type figures pre-checked in code) or with many articles go straight to the stronger model (qwen3.7-plus); the rest are drafted by qwen3.7-flash first and retried once on qwen3.7-plus if a check fails.',
   'Quote check. Every quote must be a verbatim substring of the stored text of an article from the same group (only whitespace may be repaired). A quote that fails is not published. If a group that has a summary is left with no verified quote, the card is not published.',
   'Number check. Every number in a summary and in the divergence block must occur in the source texts. A mismatch triggers a retry on the stronger model; several mismatches mean the card is not published. A figure conflict between groups is determined in code from numbers that the model cited with an article id and that the code found in that article’s text.',
   'Translations. Title, place, summaries and the divergence block are written in Russian and then machine-translated into Ukrainian and English (qwen3.7-plus). Code checks that numbers in the translation match, that script and length are plausible, and that settlement and region names and surnames match the original (for English using the GeoNames gazetteer, CC BY 4.0; on a mismatch: one retranslation with a hint, then the translation is withheld); quotes are not translated. A card that fails is not published.',
   'Publication. A card goes live automatically if it passes all quality gates (below). There is no manual review.'
  ]),
  ('p', 'Quality gates: a card is withheld if (1) it has fewer than two relevant articles or fewer than two distinct sources; (2) the relevance filter confirmed fewer than half of the clustered articles; (3) a group with a summary has no verified quote; (4) several numbers in a summary are not found in the sources; (5) the model reported high uncertainty; (6) the translation check failed; (7) the model output is incomplete or invalid. If a previously published card stops passing these checks (for example after new articles arrive), it is withdrawn automatically and the withdrawal is written to the corrections log.'),
 ]),
 ('conf', 'Confirmation scale', [
  ('p', 'Each event gets a confirmation level, computed mostly in code from formal features: how many groups and distinct sources describe the event; whether international or exile independent outlets are among them (Kyiv Independent counts as a Ukrainian outlet and is not treated as international for this purpose); whether the numbers differ (a number difference alone does not produce the “contradict” label: “launched” and “shot down” are different quantities; the label is set only when the model, reading the texts, flags incompatible statements); whether the quote and number checks passed; how far the relevance filter confirmed the grouping; how uncertain the model itself considered the card. The level is shown as an icon plus text (never colour alone).'),
  ('labels',),
  ('p', 'Important: the label describes coverage and consistency of the materials in the collected data, not truth. “Confirmed by independent sources” means independent outlets describe the event, not that they verified every detail; they may be relaying one party’s statement. The label is assigned automatically and can be wrong.'),
 ]),
 ('errors', 'What to expect: error forecast', [
  ('p', 'Below are the kinds of errors to expect and rough figures from test runs: on 2026-10-04 (OpenAI models, used for earlier cards) and in November 2026 (Qwen). Samples are small (the clustering test used 100 articles and 9 reference cards; the summary test 8 cards), so the figures are indicative and do not transfer to the whole site.'),
  ('ul', [
   'Split and merged events. In the clustering test (gpt-6-luna, default setting) on average 63% of a reference card’s key articles landed in one best cluster; for 3 of 9 cards at least 75%. The model is cautious: of 100 articles 55–69 stay without an event, and the same event is often split into two cards. Of 17 clusters, 2 were mixed (adjacent episodes), both arguable. The reference itself is loose, so the estimate is approximate.',
   'Wrong article attribution. Without a relevance filter, in one of 8 checked cards the draft presented an unrelated article (a different story) as the “Russian version” of the event. That is why the filter exists; its own accuracy was not measured separately.',
   'Shallow “Where the versions diverge” block. This is the most variable field: weaker model settings often produced generic wording instead of concrete figures and phrasings; the stronger model described divergences more precisely. The block only records differences; it does not verify facts.',
   'Quotes and numbers. In the Qwen test (November 2026) 94% of quotes were verbatim on the first attempt and the model fixed the rest on a retry; quotes that fail the check are not published. In an earlier test, all 35 of 35 quotes from gpt-6-luna (default setting) were verbatim; a weaker setting had 40 of 42 (it is not used in the pipeline). Of 25 numbers in summaries, one was not found in the source (a date). The sample is small, and about 15 claims checked by hand contained no invented facts. A verbatim quote does not guarantee that it is well chosen or representative.',
   'Translation errors. Summaries and the divergence block are translated into Ukrainian and English. The translation error rate was not measured in the test; in one sample from the stronger model an English word leaked into a Russian text, and in the Qwen test one town was replaced by another in an English translation (hence the place-name check). Code catches gross failures (numbers, language, length, place names and surnames), not subtle meaning errors.',
   'Omissions and selection bias. “No reports found” means absent from the collected data, not from media in general. The set of events is determined by automatic clustering and the availability of sources.'
  ]),
 ]),
 ('corrections', 'Corrections policy', [
  ('ul', [
   'How to report an error: the “Report an error” link on every card and in the site footer (an e-mail; the page address is filled in). The address is currently a placeholder and not configured.',
   'What happens to a report: the card is re-checked (the checks are run again on the stored texts); if the error is confirmed the card is withdrawn and, if needed, rebuilt. The project does not promise response times.',
   'Corrections log: all withdrawals and corrections are recorded on the public “Corrections” page — date, card, what happened and why. Automatic withdrawals (a card stopped passing the checks) are recorded there too and marked as automatic.',
   'A correction does not erase history: the entry stays in the log after a card is withdrawn or replaced.'
  ]),
 ]),
 ('neutrality', 'Neutrality', [
  ('ul', [
   'Claims are attributed to whoever made them (“the Russian Defense Ministry says…”, “according to the regional administration…”, “BBC writes…”); contested claims are not presented as fact.',
   'The site does not decide which version is true and does not judge the parties’ actions. The “Where the versions diverge” block records differences in figures and wording.',
   'The groups label a source’s affiliation, not its reliability: “Russia” — Russian state and state-friendly outlets; “Ukraine” — Ukrainian outlets (the public broadcaster and private ones); “Independent” — international and exile outlets plus Kyiv Independent. Some independent outlets receive state or public funding (BBC, DW, Al Jazeera); editorial independence is their own claim, which we do not verify.',
   'The choice of sources itself introduces bias: the Russian group has no outlets that are critical of the authorities and open to automated reading, and Kyiv Independent may hold a pro-Ukrainian stance.'
  ]),
 ]),
 ('anon', 'Anonymity of the project', [
  ('p', 'The project is anonymous: there are no names or personal contacts of the authors on the site or in the code. The only contact is a feedback address (currently a placeholder). The site uses no analytics counters or cookies; the browser stores only your chosen language and colour theme locally.'),
 ]),
 ('sources', 'Sources and their limitations', [
  ('p', 'The list below contains every source the pipeline uses, with its affiliation and access method. The article count is the number of stored articles in the database (including those unrelated to any event).'),
  ('sources',),
  ('p', 'Sources that were tested but are not used, or only partly usable:'),
  ('blocked',),
  ('p', 'Limitations:'),
  ('ul', [
   'Collection depth is limited by what RSS feeds, sitemaps and archives return: for RIA, BBC, France 24, Meduza, Guardian, DW, Interfax and others part of past days’ material is unavailable.',
   'For TASS in Russian only titles and leads from RSS are collected; for the Russian Defense Ministry — texts of posts in its Telegram channel (public web preview).',
   'The Russian group consists of state and state-friendly sources; the Ukrainian group of the public broadcaster and private outlets, and Kyiv Independent is “independent” only nominally. “Independence” is an approximate notion.',
   'The project does no independent fact-checking.'
  ]),
 ]),
 ('contact', 'Contact', [('contact',)]),
 ])

# ---- sources table -----------------------------------------------------------------------------------------------
# (group, db-name prefix(es), display name (ru, uk, en), affiliation/notes (ru, uk, en), access, status)
SOURCES_TABLE = [
 ('ru', ('Минобороны',), ('Минобороны РФ (Telegram)', 'Міноборони РФ (Telegram)', 'Russian Defense Ministry (Telegram)'),
  ('официальный канал ведомства; публичное веб-превью t.me/s/mod_russia (сайты mil.ru недоступны с сервера)', 'офіційний канал відомства; публічне веб-превʼю t.me/s/mod_russia (сайти mil.ru недоступні з сервера)', 'the ministry’s official channel; public web preview t.me/s/mod_russia (mil.ru sites unreachable from the server)'), 't.me/s/mod_russia', 'ok'),
 ('ru', ('ТАСС (tass.com',), ('ТАСС (tass.com)', 'ТАСС (tass.com)', 'TASS (tass.com)'),
  ('государственное информагентство РФ; английская версия, sitemap + страницы', 'державне інформагентство РФ; англійська версія, sitemap + сторінки', 'Russian state news agency; English version, sitemap + pages'), 'tass.com sitemap', 'ok'),
 ('ru', ('ТАСС (tass.ru',), ('ТАСС (tass.ru, RSS)', 'ТАСС (tass.ru, RSS)', 'TASS (tass.ru, RSS)'),
  ('только заголовок и лид из RSS: страницы статей закрыты антибот-проверкой', 'лише заголовок і лід з RSS: сторінки статей закриті антибот-перевіркою', 'title and lead from RSS only: article pages are behind an anti-bot check'), 'tass.ru/rss/v2.xml', 'mid'),
 ('ru', ('РИА',), ('РИА Новости', 'РИА Новости', 'RIA Novosti'),
  ('государственное информагентство РФ; robots.txt запрещает подгрузку /services/, поэтому читаются RSS, тег «Украина» и дневные архивы разделов — охват неполный', 'державне інформагентство РФ; robots.txt забороняє підвантаження /services/, тому читаються RSS, тег «Україна» та денні архіви розділів — охоплення неповне', 'Russian state news agency; robots.txt disallows the /services/ loader, so RSS, the “Ukraine” tag and daily section archives are read — coverage is incomplete'), 'ria.ru RSS + archives', 'mid'),
 ('ru', ('Интерфакс',), ('Интерфакс', 'Інтерфакс', 'Interfax'),
  ('частное российское информагентство, работающее легально в РФ; RSS (≈25 последних записей) + страницы', 'приватне російське інформагентство, що легально працює в РФ; RSS (≈25 останніх записів) + сторінки', 'private Russian news agency operating legally in Russia; RSS (≈25 latest items) + pages'), 'interfax.ru/rss.asp', 'mid'),
 ('ua', ('Суспільне',), ('Суспільне', 'Суспільне', 'Suspilne'),
  ('публичный вещатель Украины; sitemap + RSS', 'публічний мовник України; sitemap + RSS', 'Ukraine’s public broadcaster; sitemap + RSS'), 'suspilne.media sitemaps', 'ok'),
 ('ua', ('Українська',), ('Українська правда', 'Українська правда', 'Ukrainska Pravda'),
  ('частное украинское издание; архивы по дням', 'приватне українське видання; архіви за днями', 'private Ukrainian outlet; daily archives'), 'pravda.com.ua archives', 'ok'),
 ('ua', ('РБК',), ('РБК-Украина', 'РБК-Україна', 'RBC-Ukraine'),
  ('частное украинское издание; RSS с полным текстом (≈3 суток)', 'приватне українське видання; RSS з повним текстом (≈3 доби)', 'private Ukrainian outlet; RSS with full text (≈3 days)'), 'rbc.ua RSS', 'ok'),
 ('ind', ('BBC',), ('BBC News', 'BBC News', 'BBC News'),
  ('британский общественный вещатель (финансируется лицензионным сбором); RSS (≈100 свежих статей на весь BBC)', 'британський суспільний мовник (фінансується ліцензійним збором); RSS (≈100 свіжих статей на весь BBC)', 'British public broadcaster (licence-fee funded); RSS (≈100 recent articles for all of BBC)'), 'bbc RSS', 'ok'),
 ('ind', ('The Moscow Times',), ('The Moscow Times', 'The Moscow Times', 'The Moscow Times'),
  ('независимое англоязычное издание в изгнании (Амстердам); в РФ признано «нежелательной организацией»; RSS + карта сайта', 'незалежне англомовне видання у вигнанні (Амстердам); у РФ визнано «небажаною організацією»; RSS + мапа сайту', 'independent English-language exile outlet (Amsterdam); designated an “undesirable organisation” in Russia; RSS + sitemap'), 'themoscowtimes.com RSS + sitemap', 'ok'),
 ('ind', ('France 24',), ('France 24', 'France 24', 'France 24'),
  ('международный вещатель Франции (France Médias Monde, финансируется государством); RSS разделов', 'міжнародний мовник Франції (France Médias Monde, фінансується державою); RSS розділів', 'French international broadcaster (France Médias Monde, state-funded); section RSS'), 'france24.com RSS', 'ok'),
 ('ind', ('The Guardian',), ('The Guardian', 'The Guardian', 'The Guardian'),
  ('британская газета, принадлежит траст Scott Trust; RSS «Украина» + страницы', 'британська газета, належить трасту Scott Trust; RSS «Україна» + сторінки', 'British newspaper owned by the Scott Trust; “Ukraine” RSS + pages'), 'theguardian.com RSS', 'ok'),
 ('ind', ('DW',), ('DW (EN, RU)', 'DW (EN, RU)', 'DW (EN, RU)'),
  ('международный вещатель Германии, финансируется из федерального бюджета (редакционная независимость закреплена законом); RSS + страницы', 'міжнародний мовник Німеччини, фінансується з федерального бюджету (редакційна незалежність закріплена законом); RSS + сторінки', 'German international broadcaster funded from the federal budget (editorial independence is set by law); RSS + pages'), 'rss.dw.com', 'ok'),
 ('ind', ('Al Jazeera',), ('Al Jazeera', 'Al Jazeera', 'Al Jazeera'),
  ('катарский вещатель, финансируется государством Катар; RSS + страницы (по украинской тематике материалов мало)', 'катарський мовник, фінансується державою Катар; RSS + сторінки (за українською тематикою матеріалів мало)', 'Qatari broadcaster funded by the state of Qatar; RSS + pages (few items on Ukraine)'), 'aljazeera.com RSS', 'ok'),
 ('ind', ('Meduza',), ('Meduza (RU, EN)', 'Meduza (RU, EN)', 'Meduza (RU, EN)'),
  ('независимое издание в изгнании (Латвия); RSS с полным текстом (≈30 последних материалов на язык); в РФ признано «нежелательной организацией»', 'незалежне видання у вигнанні (Латвія); RSS з повним текстом (≈30 останніх матеріалів на мову); у РФ визнано «небажаною організацією»', 'independent exile outlet (Latvia); RSS with full text (≈30 latest items per language); designated an “undesirable organisation” in Russia'), 'meduza.io RSS', 'ok'),
 ('ind', ('Медиазона',), ('Медиазона', 'Медіазона', 'Mediazona'),
  ('независимое издание (основано в 2014 г., работает из-за рубежа); в РФ признано «иностранным агентом»; RSS + страницы', 'незалежне видання (засноване 2014 р., працює з-за кордону); у РФ визнано «іноземним агентом»; RSS + сторінки', 'independent outlet (founded 2014, works from abroad); designated a “foreign agent” in Russia; RSS + pages'), 'zona.media/rss', 'ok'),
 ('ind', ('Euronews',), ('Euronews (EN)', 'Euronews (EN)', 'Euronews (EN)'),
  ('европейский телеканал (основной владелец — Alpac Capital, Португалия; софинансирование ЕС); RSS + страницы; русская версия недоступна для автоматического чтения (HTTP 406)', 'європейський телеканал (основний власник — Alpac Capital, Португалія; співфінансування ЄС); RSS + сторінки; російська версія недоступна для автоматичного читання (HTTP 406)', 'European TV channel (majority owner Alpac Capital, Portugal; EU co-funding); RSS + pages; the Russian edition refuses automated access (HTTP 406)'), 'euronews.com/rss', 'ok'),
 ('ind', ('Kyiv Independent',), ('Kyiv Independent', 'Kyiv Independent', 'Kyiv Independent'),
  ('украинское частное англоязычное издание; позиция может быть проукраинской — в «независимых» условно', 'українське приватне англомовне видання; позиція може бути проукраїнською — у «незалежних» умовно', 'Ukrainian private English-language outlet; stance may be pro-Ukrainian — “independent” only nominally'), 'kyivindependent.com sitemap', 'ok'),
]
BLOCKED = [
 (('Reuters', 'Reuters', 'Reuters'), ('HTTP 401: требуется JS/cookies — не используется', 'HTTP 401: потрібні JS/cookies — не використовується', 'HTTP 401: needs JS/cookies — not used')),
 (('Укрінформ / Ukrinform', 'Укрінформ / Ukrinform', 'Ukrinform'), ('ukrinform.ua и .net отвечают 403 на любые запросы — не используется', 'ukrinform.ua і .net відповідають 403 на будь-які запити — не використовується', 'ukrinform.ua and .net answer 403 to any request — not used')),
 (('Коммерсантъ', 'Коммерсантъ', 'Kommersant'), ('RSS отдаётся, но robots.txt запрещает автоматическое чтение страниц статей — не используется', 'RSS віддається, але robots.txt забороняє автоматичне читання сторінок статей — не використовується', 'RSS is served but robots.txt disallows automated reading of article pages — not used')),
 (('Lenta.ru', 'Lenta.ru', 'Lenta.ru'), ('robots.txt запрещает автоматическое чтение страниц статей — не используется', 'robots.txt забороняє автоматичне читання сторінок статей — не використовується', 'robots.txt disallows automated reading of article pages — not used')),
 (('РБК (rbc.ru)', 'РБК (rbc.ru)', 'RBC (rbc.ru)'), ('HTTP 401 — не используется', 'HTTP 401 — не використовується', 'HTTP 401 — not used')),
 (('ISW', 'ISW', 'ISW'), ('страницы отчётов отдаются, но это аналитические дайджесты, а не репортажи об отдельных событиях — не используется', 'сторінки звітів віддаються, але це аналітичні дайджести, а не репортажі про окремі події — не використовується', 'report pages are served but they are analytical digests rather than reports on single events — not used')),
 (('UNIAN', 'УНІАН', 'UNIAN'), ('RSS отдаётся (robots.txt не запрещает), но источники пока не подключены — можно добавить позже', 'RSS віддається (robots.txt не забороняє), але джерела поки не підключені — можна додати пізніше', 'RSS is served (robots.txt does not disallow) but these sources are not connected yet — may be added later')),
 (('Генштаб ВСУ (zsu.gov.ua)', 'Генштаб ЗСУ (zsu.gov.ua)', 'Ukrainian General Staff (zsu.gov.ua)'), ('сайт отдаёт 200, но содержимое строится JS; напрямую не собирается (его сводки цитируют собранные украинские издания)', 'сайт віддає 200, але вміст будується JS; напряму не збирається (його зведення цитують зібрані українські видання)', 'the site returns 200 but content is rendered by JS; not collected directly (its briefings are quoted by the collected Ukrainian outlets)')),
]
