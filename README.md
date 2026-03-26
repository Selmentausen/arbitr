# Arbitr - Система для извлечения и фильтрации релевантных судебных дел

Система для автоматизированного сбора, фильтрации и анализа судебных дел с сайта [kad.arbitr.ru](https://kad.arbitr.ru) (Арбитражные суды РФ). Основная цель — идентификация релевантных дел, где стороны склонны к медиации, с извлечением информации о клиенте.

## 📋 Содержание

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Установка](#установка)
- [Использование](#использование)
- [Разработка](#разработка)
- [Тестирование](#тестирование)
- [Структура проекта](#структура-проекта)

## ✨ Возможности

- **Многоэтапная фильтрация**: Скрининг на основе базовых данных, анализ HTML, парсинг PDF, LLM-анализ
- **Категоризация**: Настраиваемые правила для областей права (строительный подряд, банкротство и др.)
- **Анализ связей**: Выявление связанных дел по истцу/ответчику, агрегирование метрик
- **Масштабируемость**: Обработка больших объемов данных (45+ млн дел) с батч-процессингом
- **Модульная архитектура**: Легко расширяется на новые области права и суды

## 🏗️ Архитектура

Система построена как модульный пайплайн с разделением на слои:

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Scraper   │ --> │    Filter    │ --> │   Linkage   │
│    Layer    │     │   Pipeline   │     │   Module    │
└─────────────┘     └──────────────┘     └─────────────┘
                           │
                           v
                    ┌──────────────┐     ┌─────────────┐
                    │   Storage    │ <-- │   Config    │
                    │    Layer     │     │   Manager   │
                    └──────────────┘     └─────────────┘
```

**Компоненты:**
- **Scraper Layer**: Сбор данных с использованием Playwright (имитация браузера)
- **Filter Pipeline**: 4-этапная фильтрация (initial screen, HTML, PDF, LLM)
- **Linkage Module**: Анализ связей между делами и агрегирование метрик
- **Storage Layer**: PostgreSQL для структурированных данных, Redis для кэширования
- **Config Manager**: YAML-конфигурация правил фильтрации

## 🚀 Установка

### Требования

- Python 3.10+
- [Poetry](https://python-poetry.org/) для управления зависимостями
- Docker и Docker Compose (опционально, для контейнеризации)

### Установка через Poetry

1. **Клонируйте репозиторий и перейдите в директорию проекта:**
   ```bash
   cd d:\dev\2026\Arbitr
   ```

2. **Установите зависимости через Poetry:**
   ```bash
   poetry install
   ```

3. **Установите браузеры Playwright (для scraping):**
   ```bash
   poetry run playwright install chromium
   ```

### Установка через Docker

1. **Соберите Docker образ:**
   ```bash
   docker-compose build
   ```

2. **Запустите сервисы (PostgreSQL, Redis):**
   ```bash
   docker-compose up -d postgres redis
   ```

## 💻 Использование

### Базовая конфигурация

Все конфигурации находятся в файле `configs/main.yaml`. Пример настройки фильтров:

```yaml
areas:
  construction:
    keywords: ["подряд", "строительство", "договор подряда"]
    weight: 30

thresholds:
  high: 80
  low: 20
```

### Примеры использования

**Загрузка конфигурации:**
```python
from src.config import load_config, get_rules

# Загрузить конфигурацию
config = load_config('configs/main.yaml')

# Получить правила для области "строительный подряд"
construction_rules = get_rules('construction')
print(construction_rules['keywords'])
```

**Работа с моделями данных:**
```python
from src.models import CaseBase, Case, StatusEnum

# Создать базовый кейс
case = CaseBase(
    id="A40-123456/2024",
    court="Арбитражный суд города Москвы",
    plaintiff="ООО 'Строитель'",
    defendant="ООО 'Заказчик'"
)

# Преобразовать в полную модель для фильтрации
full_case = Case(**case.model_dump())
full_case.category = "construction"
full_case.relevance_score = 75.5
full_case.status = StatusEnum.HIGH_RELEVANT
```

**Настройка логирования:**
```python
from src.utils import setup_logging, get_logger

# Настроить логирование
setup_logging(level="INFO", log_file="logs/arbitr.log")

# Получить logger для модуля
logger = get_logger(__name__)
logger.info("Processing started")
```

## 🛠️ Разработка

### Структура проекта

```
Arbitr/
├── src/                    # Исходный код
│   ├── models/             # Pydantic модели данных
│   ├── config/             # Управление конфигурацией
│   ├── scraper/            # Слой сбора данных (Phase 2)
│   ├── filters/            # Пайплайн фильтрации (Phase 3)
│   ├── storage/            # Работа с БД (Phase 4)
│   ├── linkage/            # Анализ связей (Phase 5)
│   └── utils/              # Утилиты (логирование и др.)
├── configs/                # YAML конфигурации
├── tests/                  # Тесты
├── logs/                   # Логи приложения
├── data/                   # Временные данные
├── pyproject.toml          # Poetry конфигурация
├── Dockerfile              # Docker образ
└── docker-compose.yml      # Docker Compose конфигурация
```

### Форматирование кода

Проект использует **Black** для форматирования и **isort** для сортировки импортов:

```bash
poetry run black src/ tests/
poetry run isort src/ tests/
```

### Проверка типов

```bash
poetry run mypy src/
```

## 🧪 Тестирование

### Запуск всех тестов

```bash
poetry run pytest
```

### Запуск тестов с покрытием

```bash
poetry run pytest --cov=src --cov-report=html
```

### Запуск конкретных тестов

```bash
# Тесты конфигурации
poetry run pytest tests/test_config.py -v

# Тесты моделей
poetry run pytest tests/test_models.py -v
```

## 📦 Контейнеризация

### Сборка и запуск

```bash
# Собрать все сервисы
docker-compose build

# Запустить PostgreSQL и Redis
docker-compose up -d postgres redis

# Проверить статус
docker-compose ps

# Просмотр логов
docker-compose logs -f postgres
```

### Остановка сервисов

```bash
docker-compose down

# С удалением volumes
docker-compose down -v
```

## 📝 Этапы разработки

- [x] **Phase 1**: Setup и инфраструктура
  - [x] Poetry, структура проекта
  - [x] Pydantic модели (CaseBase, Case)
  - [x] Config Manager (YAML)
  - [x] Logging система
  - [x] Docker setup
  - [x] Тесты
- [ ] **Phase 2**: Scraper Layer (Playwright, BeautifulSoup)
- [ ] **Phase 3**: Filter Pipeline (4-этапная фильтрация)
- [ ] **Phase 4**: Storage Layer (PostgreSQL, SQLAlchemy)
- [ ] **Phase 5**: Linkage Module (анализ связей)
- [ ] **Phase 6**: LLM Integration (OpenAI/Grok)
- [ ] **Phase 7**: Оптимизация и интеграция

## 📄 Лицензия

Внутренний проект

## 👥 Авторы

Arbitr Team

---

**Статус проекта**: В разработке (Phase 1 завершена)
