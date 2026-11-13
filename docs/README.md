# Документация Cyber102

Документы разделены по назначению. Для работы Тимура начните с [TIMUR](../TIMUR/README.md), для разработки и защиты проекта — с инженерного пакета.

| Раздел | Материалы |
| --- | --- |
| Архитектура | [База данных и API](architecture/database.md), [локальное зрение](architecture/vision.md) |
| Работа стенда | [Развёртывание на 3090, запуск и телефоны](operations/deployment.md) |
| Чекпоинты | [Первый чекпоинт и сценарий показа](checkpoints/checkpoint-1.md) |
| ML | [Исследование CatBoost и AutoGluon](ml/ml-research.md), [эксперимент UCI](ml/benchmark-uci.md), [его фактические метрики](ml/uci-benchmark-report.json) |
| Инженерный пакет | [Оглавление](research/README.md), [главы](research/chapters/), [первоисточники](research/sources/README.md), [каталог данных](research/datasets/catalog.json) |
| Контракты данных | [Схема телеметрии](research/schemas/telemetry-schema.json), [схема разметки](research/schemas/annotation-schema.json) |
| Готовые материалы | [Word, PDF и ZIP](../output/README.md), [результаты проверки пакета](research/validation-report.json) |

`research/schemas/` описывает предлагаемые форматы подготовки данных. Действующий HTTP API описан в `architecture/`; перенос файлов не меняет маршруты сервера.

Новые документы сохраняем в соответствующий раздел и добавляем сюда ссылку. Источники и примеры храним рядом с тематическим пакетом, готовые экспорты — в `output/`. Заводские данные, секреты и временные результаты в документацию не включаем.
