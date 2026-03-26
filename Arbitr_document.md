# Документ: Дизайн архитектуры системы для извлечения и фильтрации релевантных судебных дел

**Версия:** 0.5 (Черновик)  
**Дата:** 07 февраля 2026

## **1\. Введение**

### 1.1 Обзор проекта

Система предназначена для автоматизированного сбора, фильтрации и анализа судебных дел с сайта kad.arbitr.ru (Арбитражные суды РФ). Основная цель — идентификация релевантных дел, где стороны склонны к медиации, с извлечением информации о клиенте (истец/ответчик и связанные данные). Фильтрация многоэтапная, учитывает суды, судей, группы приставов, области права и ключевые сигналы релевантности.  
Прототип фокусируется на области "строительный подряд". Система должна быть модульной для расширения на другие области без полной перестройки.  
Учитывая объем данных (45+ млн дел), подход инкрементальный: начинать с конкретных судов/регионов, обрабатывать данные порциями (батчами), с оптимизацией для снижения нагрузки на сайт.

### 1.2 Ключевые ограничения и предположения

* **Технические ограничения сайта:**  
  * **Защита:** DDOS-Guard, PravoCaptcha, SPA на React/Vue, что усложняет парсинг.  
  * **Доступ к API:** Ограничен, требует имитации реального пользователя (headers, cookies, user-agent ротация, delays между запросами). Нет прямого API для всех данных; использовать только через браузер-подобный доступ.  
  * **Объем:** 45+ млн дел. Фокус на ежедневных/еженедельных обновлениях, фильтрация по судам.  
* **Разработка:** 3-4 месяца на MVP. Приоритет простота, тестируемость, расширяемость.  
* **Предположения:**  
  * Доступны резидентские прокси  
  * **Хранение:** S3 подобное для расширяемости  
  * **Интеграция LLM:** Для финальной фильтрации (локальный или API).  
  * Для прототипа фокус на рабочем результате без строгих ограничений на scraping;

### 1.3 Цели архитектуры

* **Эффективность**: Минимизировать запросы к сайту (фильтровать на ранних этапах).  
* **Расширяемость**: Конфигурируемые правила для областей права, судей/приставов.  
* **Надежность**: Обработка ошибок (пустые поля, CAPTCHA), логирование.

## **2\. Обзор требований**

### 2.1 Функциональные требования

* **Сбор данных:**  
  * Парсинг главной страницы поиска дел (фильтры по датам, судам).  
  * Доступ к страницам дел (HTML), деталям (PDF с текстом).  
* **Фильтрация (многоэтапная):**  
  * Начальный скрининг: На основе видимых данных (дело, суд, судьи, истец, ответчик, третье лицо). Категории: высоко релевантные, отклонить, недостаточно информации, иное. Категоризация по области.  
  * Анализ HTML страницы дела: Дополнительные данные, score релевантности.  
  * Анализ PDF: Извлечение текста, уточнение.  
  * LLM: Для неопределенных дел.  
* **Категоризация и релевантность:**  
  * Области: Конфигурируемые (YAML) правила.  
  * Группы приставов: Маппинг по регионам/судам (e.g., Москва: группа X \-\> строительство и банкротство).  
  * Score: Weighted сумма (e.g., 0-100), пороги для перехода этапов.  
* **Связи между кейсами:** Хранение и использование связей (e.g., по истцу/ответчику) для агрегированного анализа: количество дел, длительность, исходы (завершены ли, как). Повторный проход фильтрации для обновления статуса (e.g., "недостаточно info" \-\> "relevant" на основе истории споров).  
* **Хранение и вывод:**  
  * БД для дел, метаданных, сущностей (истцы/ответчики) и отношения.  
  * Экспорт: CSV/JSON для клиента.  
* **Оптимизация:** Пропуск этапов если score высокий/низкий.

### 2.2 Нефункциональные требования

* **Производительность:** Обработка 1000 дел/день (начально), \<5 сек/дело.  
* **Масштабируемость:** Легко добавить области/суды.  
* **Доступность:** Локальный запуск, позже — контейнеризация (Docker).  
* **Логирование:** Полное трассировка для отладки.  
* **Тестируемость:** Unit/integration тесты.

### 2.3 Риски и mitigation

* **Риск:** Блокировка сайта. **Mitigation:** Ротация IP/user-agent, задержки (5-10 сек), мониторинг ошибок.  
* **Риск:** Изменения сайта. **Mitigation:** Модульный парсер, регулярные тесты.  
* **Риск:** Объем данных. **Mitigation:** Батч-процессинг, индексация в БД.  
* **Риск:** Производительность при анализе связей (для миллионов дел). **Mitigation:** Индексация по сущностям, кэширование агрегатов, инкрементальные обновления.

## **3\. Высокоуровневая архитектура**

### 3.1 Обзор компонентов

Система построена как модульный пайплайн (pipeline) с разделением на слои: сбор данных, обработка/фильтрация, хранение и анализ. Это позволяет независимую разработку и тестирование модулей. Архитектура монолитная для прототипа (один Python-скрипт/приложение), но с возможностью перехода к микросервисам (например, via FastAPI) при масштабе.  
Ключевые компоненты:

* **Scraper Layer:** Отвечает за сбор данных с kad.arbitr.ru. Использует (Selenium или Playwright) для имитации пользователя, обхода CAPTCHA и парсинга SPA.  
* **Filter Pipeline:** Многоэтапный процессор, где каждый этап — отдельный модуль с правилами (rule-based) и score.  
* **Linkage Module:** Отвечает за выявление и хранение связей между кейсами (e.g., по entities), aggregation метрик и переоценка.  
* **Storage Layer:** База данных для промежуточных и финальных данных (PostgreSQL для структуры, возможно Elasticsearch для текста).  
* **Analysis Layer:** Интеграция LLM для финальной классификации.  
* **Config Manager:** Центральный хранитель конфигов (YAML/JSON) для правил, порогов, маппингов.  
* **Logger & Monitor:** Сквозное логирование (logging модуль Python) для отладки.

### 3.2 Поток данных (High-Level Flow)

1. **Вход:** Запуск скрипта с параметрами (e.g., дата диапазон, суды для сбора).  
2. **Сбор:** Scraper берет список дел \-\> парсит базовые данные \-\> сохраняет в временное хранилище.  
3. **Фильтрация:** Для каждого дела последовательный вызов этапов (if score \> threshold, пропускаем дальнейшую фильтрацию).  
4. **Linkage & Re-evaluation:** После батча выявить связи, агрегированные данные, повторно обработать "uncertain" кейсы на основе агрегированной информации.  
5. **Анализ:** Если нужно, LLM prompt с извлеченными данными (включая aggregated от linkage).  
6. **Выход:** Финальные релевантные дела в БД/файл, с извлеченной клиентской информации и ссылками.

Текстовое представление диаграммы (UML-like):  
text  
\[Input Params\] **\--\>** \[Scraper\] **\--\>** \[Raw Data Queue\]  
Raw Data Queue **\--\>** \[Stage 1 Filter (Initial Screen)\] **\--\>** \[Categorized Data\]  
**If** "Insufficient Info" **or** "Medium Score" **\--\>** \[Stage 2 Filter (HTML Parse)\] \--\> \[Updated Score\]  
**If** still uncertain **\--\>** \[Stage 3 Filter (PDF Parse)\] **\--\>** \[Refined Data\]  
All Data **\--\>** \[Linkage Module\] **\--\>** \[Aggregated Links & Metrics\]  
**If** gray zone or insufficient **\--\>** \[Re-evaluate with Links\] **\--\>** \[Final Category\]  
**If** still gray **\--\>** \[LLM Analyzer\] **\--\>** \[Final Category\]  
All Stages **\--\>** \[Storage DB\] **\--\>** \[Export (CSV/JSON)\]  
Config Manager **\<--\>** All Filters (rules injection)  
Logger **\<--\>** All Components

### 3.3 Интеграции и зависимости

* **Внешние:** Headless browser для сайта (нужно проверить можно ли обойти защиту), PDF parser (PyMuPDF), LLM API (если не локальный).  
* **Внутренние:** Модули общаются через data classes (Pydantic для валидации) или queues (для асинхронности, via asyncio).

## **4\. Детальный дизайн модулей**

### 4.1 Scraper Layer

Этот модуль отвечает за сбор данных, интегрированный с фильтрацией для избежания ненужных запросов. Scraping происходит лениво: базовый сбор (список дел) всегда, но углубленный (HTML страницы, PDF) только если предыдущий этап фильтрации требует (conditional scraping).

* **Функции:**  
  * ***fetch\_case\_list(params: dict) \-\> list\[CaseBase\]***: Получает список дел по фильтрам (дата, суд). Использует headless browser (надо проверить) для навигации по поиску, обхода CAPTCHA (интеграция с солвером, e.g., 2Captcha или ручной ввод для теста). Парсит таблицу результатов (visible data: номер дела, суд, судьи, истец, ответчик).  
  * ***fetch\_case\_html(case\_id: str) \-\> str***: Загружает HTML страницы дела, если stage 1 требует углубления. Имитирует клик/навигацию.  
  * ***fetch\_case\_pdf(case\_id: str, pdf\_links: list) \-\> list\[bytes\]***: Скачивает PDF (с текстом), только если stage 2 не definitive.  
* **Интеграция с фильтрацией:** Scraper вызывается из Filter Pipeline. Например, после stage 1, если категория "недостаточно информации" или score в "серой зоне" (e.g., 40-60), то *scraper.fetch\_case\_html()*. Это минимизирует трафик: для rejected/high relevance на раннем этап, не парсим дальше.  
* **Обработка ошибок:** Retry на CAPTCHA/block (с задержкой), fallback если поле пустое (прим. нет судьи, помечаем и идем дальше).  
* **Оптимизация:** Batch fetching (10 дел за раз), ротация прокси (resident proxies), user-agent рандомизация.

### 4.2 Filter Pipeline

Центральный модуль для многоэтапной фильтрации. Каждый этап это функция, возвращающая обновленный Case object с категорией, оценкой и извлеченными данными. Pipeline manager вызывает этапы последовательно, с проверками на пороги. Добавлена поддержка переоценки на основе связей.

* **Структура Case (data class, via Pydantic):**  
  1. id: str  
  2. court: str  
  3. judges: list\[str\] (may be empty)  
  4. plaintiff: str  
  5. defendant: str  
  6. category: str (e.g., "construction")  
  7. relevance\_score: float (0-100)  
  8. status: enum ("high\_relevant", "reject", "insufficient\_info", "uncertain")  
  9. extracted\_data: dict (e.g., {"client\_info": "...", "duration": days, "outcome": "settled"})  
  10. related\_cases: list\[str\] (ids связанных дел)  
  11. aggregated\_metrics: dict (e.g., {"dispute\_count": 5, "avg\_duration": 120, "mediation\_rate": 0.4})  
  12. raw\_html: str (optional, filled on stage 2\)  
  13. pdf\_texts: list\[str\] (optional, from stage 3\)  
* **Этапы (функции):**  
  1. ***stage1\_initial\_screen(case: CaseBase) \-\> Case:*** На основе базовых данных. Применяет правила (keywords in plaintiff/defendant, judge group mapping). Assign category/score. If score \> 80 \-\> "high\_relevant", skip further. If \<20 \-\> "reject". Else \-\> "insufficient\_info" or "uncertain" \-\> proceed.  
  2. ***stage2\_html\_analyze(case: Case) \-\> Case:*** Если нужно, вызывает scraper.fetch\_case\_html(), парсит (BeautifulSoup), extracts more (e.g., keywords from description). Update score. Similar thresholds.  
  3. ***stage3\_pdf\_analyze(case: Case) \-\> Case:*** Если еще uncertain, fetch PDFs, extract text (PyMuPDF), search for signals (regex/keywords). Refine (e.g., extract duration, outcome if available).  
  4. ***stage4\_llm\_analyze(case: Case) \-\> Case:*** Для gray zone (configurable range), prompt LLM (e.g., "Analyze if case shows mediation potential: \[text \+ aggregated\_metrics\]").  
* ***Pipeline Manager:process\_batch(cases: list\[CaseBase\]) \-\> list\[Case\]:*** Вызывает этапы для каждого, затем вызывает Linkage Module на батче, переоценивает "insufficient\_info"/"uncertain" с aggregated data (e.g., if dispute\_count \> 3 and mediation\_rate \> 0.3 \-\> boost score \+20). Логирует решения.  
* **Расширяемость:** Каждый этап берет правила из Config Manager; переоценивает правила в конфиге (e.g., weights для aggregates).

### **4.3 Config Manager**

Модуль для регулируемости. Загружает YAML/JSON файлы при старте.  
**Пример конфига (YAML):**  
	areas:  
  construction:  
    keywords: \["подряд", "строительство", "договор подряда"\]  
    party\_combos: \["юр.лицо vs юр.лицо"\]  
    weight: 30  \# for score  
judge\_groups:  
  moscow:  
    group1: \["construction", "bankruptcy"\]  
thresholds:  
  high: 80  
  low: 20  
  gray\_min: 40  
  gray\_max: 60  
linkage\_rules:  
  dispute\_count\_threshold: 3  
  mediation\_rate\_weight: 10  \# \+score per 0.1 rate

* **Функции:** *load\_config(file: str) \-\> dict*, *get\_rules(area: str) \-\> dict*. Позволяет hot-reload для тестов.

### **4.4 Linkage Module**

Новый модуль для обработки связей между кейсами. Работает после начальной фильтрации батча, использует Storage для запросов.

* **Функции:**  
  * ***build\_links(cases: list\[Case\]) \-\> None:*** Для каждого кейса нормализует сущности (plaintiff/defendant как уникальные IDs, через hashing или dedup), сохраняет связи в БД (таблица disputes: plaintiff\_id, defendant\_id, case\_id).  
  * ***aggregate\_metrics(entity\_pair: tuple\[str, str\]) \-\> dict:*** Query БД для пары истец/ответчик: счетчик дел, средняя продолжительность (из extracted\_data), mediation\_rate (% с "мировое соглашение" в outcome). Кэширует для скорости.  
  * ***re\_evaluate(case: Case, aggregates: dict) \-\> Case:*** Обновляет score/status на основе правил (**if** aggregates\["dispute\_count"\] \> threshold \-\> status \= "high\_relevant").  
* **Интеграция:** Вызывается из Pipeline Manager после Стадии 1-4. Для больших объемов batch queries (SQL joins).  
* **Хранение связей:** В Storage Layer: Таблицы сущностей (id, name, type: plaintiff/defendant), связи (entity1\_id, entity2\_id, case\_id, metrics). Это позволяет делает графо-подобные запросы ("все дела для сущности X").  
* **Оптимизация:** Индексация по сущности names/ids, incremental build (только новые кейсы). Для прототипа — in-memory dict для малых батчей.

## **5\. Технологии и инструменты**

### **5.1 Язык программирования и окружение**

* **Python 3.10+:** Основной язык для всей системы.  
* **Асинхронность:** asyncio для параллельного scraping (batch fetching), чтобы ускорить обработку без блокировки.

### **5.2 Библиотеки для scraping и парсинга**

* **Playwright:** Для headless browser. Playwright (более современный, асинхронный, лучше обходит SPA/CAPTCHA). Функции: Навигация, клики, парсинг динамического контента.  
* **BeautifulSoup (bs4):** Для парсинга HTML (извлечение данных из страницы дела). Комбинировать с Playwright для полного HTML.  
* **PyMuPDF (fitz) или pdfplumber:** Для извлечения текста из PDF (с текстовыми слоями, без OCR).

### **5.3 Библиотеки для данных и модели**

* **Pydantic:** Для data classes (Case, configs). Обеспечивает валидацию, typing.  
* **Pandas:** Для манипуляции данными в батчах (aggregation в Linkage Module). Опционально для экспорта CSV.  
* **re (regex):** Для поиска по ключевым словам в правилах.

### **5.4 Хранение данных**

* **PostgreSQL:** Основная БД для структурированных данных (cases, entities, relations). Использовать SQLAlchemy для ORM (модели таблиц, queries). Масштабируема для 45+ млн записей, индексация для быстрых joins по entities.  
* **Redis:** Для кэширования aggregates в Linkage Module, если объем большой.  
* **Elasticsearch (возможно):** Для полнотекстового поиска по pdf\_texts, если нужны продвинутые запросы.

### **5.5 LLM интеграция**

Нужно проработать

### **5.6 Конфигурация и логи**

* **PyYAML или json:** Для загрузки конфигов.  
* **logging (built-in):** С handlers для file/console. Уровни: DEBUG для деталей, INFO для прогресса.

### **5.7 Тестирование и деплой**

* **Pytest:** Для unit/integration тестов (mock scraper, test filters).  
* **Docker:** Для контейнеризации (локальный запуск, scalability). Dockerfile для всего приложения.  
* **Другие:** httpx для API calls (если LLM внешний); fake-useragent для рандомизации.

### **5.8 Обоснование выбора**

* Фокус на open-source/free инструментах для прототипа (кроме прокси/CAPTCHA solvers).  
* **Расширяемость:** Модульный код (classes/functions), configs для изменений без перекомпиляции.  
* **Производительность:** Асинхронность, batching, lazy loading для большого объема.

## **6\. План реализации и таймлайн**

### **6.1 Общий подход**

Разработка — итеративная, с еженедельными целями для проверки с клиентом. Начать с основы (scraper \+ базовая фильтрация), затем добавить слои. Использовать Git для контроля версий. Общий таймлайн: 12-16 недель (3-4 месяца), с буфером на debugging/CAPTCHA проблемы.

### **6.2 Этапы реализации**

1. **Setup и инфраструктура (Неделя 1):**  
   * Установить окружение: Python, Poetry для deps, Docker для local run.  
   * Настроить configs (YAML шаблон), logging.  
   * Тестировать прокси, CAPTCHA solver и возможные обходы защиты для сокращения траффика.  
   * Создать базовую структуру: directories (src/scraper, src/filters, etc.), data classes (Case via Pydantic).  
   * **Цель:** Запуск пустого скрипта, load config.  
2. **Scraper Layer (Неделя 3-4):**  
   * Реализовать fetch\_case\_list: Playwright для поиска, парсинг таблицы (batch 10-50 дел).  
   * Добавить lazy functions: fetch\_case\_html, fetch\_case\_pdf (conditional call).  
   * Обработка ошибок: retries, delays (5-10 сек), user-agent rotate.  
   * Тест на малом объеме (e.g., 100 дел из Москвы).  
   * **Цель:** Сбор базовых данных, сохранять в JSON для отладки.  
3. **Filter Pipeline. Базовые этапы (Неделя 5-7):**  
   * Имплементировать stage1\_initial\_screen: rules matching (keywords, judge groups from config).  
   * Добавить stage2\_html\_analyze: Parse HTML (BeautifulSoup), update score.  
   * Stage3\_pdf\_analyze: Extract text, regex for signals.  
   * Pipeline manager: process\_batch, thresholds checks, skip logic.  
   * Integrate scraper for conditional fetching.  
   * **Цель:** Полный пайплайн на тестовых данных, проверка точности на тестовых данных (ручная проверка).  
4. **Storage Layer (Неделя 8):**  
   * Setup PostgreSQL (local via Docker), SQLAlchemy models: tables for cases, entities, relations.  
   * Functions: save\_case, query aggregates (simple SQL).  
   * Incremental save for batches.  
   * **Цель:** Хранение/получение данных, базовый экспорт CSV/JSON.  
5. **Linkage Module и Re-evaluation (Неделя 9-10):**  
   * Build\_links: Normalize entities (e.g., hash names for unique IDs), save relations.  
   * Aggregate\_metrics: SQL queries (count, avg).  
   * Re\_evaluate: Update score based on linkage\_rules.  
   * Integrate into pipeline (post-batch call).  
   * **Цель:** Тест на связанных кейсах (synthetic data).  
6. **Analysis Layer (LLM) (Week 11):**  
   * Integrate OpenAI/Grok API, prompt template from config.  
   * Stage4\_llm\_analyze: For gray zone, include aggregates.  
   * Fallback if API down (e.g., rule-based).  
   * Milestone: End-to-end run with LLM.  
7. **Integration, Optimization and Export (Week 12-13):**  
   * Full pipeline: Input params \-\> output relevant cases.  
   * Optimization: Async (asyncio for parallel fetch), caching (Redis if needed).  
   * Dashboard? (Optional: Streamlit for view results).  
   * Milestone: MVP demo для клиента.  
8. **Буфер и доработки (Неделя 14-16):**  
   * Fix issues (site changes, performance).  
   * Expand to other areas (add config entries).  
   * Documentation.

### **6.3 Timeline summary**

* **Месяц 1:** Setup \+ Scraper (focus on early stages).  
* **Месяц 2:** Filters \+ Storage.  
* **Месяц 3:** Linkage \+ LLM \+ Integration.  
* **Месяц 4:** Optimizations, testing, client review.

