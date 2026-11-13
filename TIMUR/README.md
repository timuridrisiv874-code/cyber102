# Тимур — веб-приложение и интеграция

Тимур отвечает за общую вебку для iPhone Safari и Android WebView, API диагностики и журнал осмотров. С участником 3 он согласует адрес приложения, вход и разрешения камеры в Android-оболочке.

Для команды подготовлены [описание продукта](product-guide.md) и [пакет первого чекпоинта](checkpoint-1-speaker-kit.md): шесть слайдов, текст спикера, показ на пять и две минуты, проверка APK и ответы жюри. Редактируемые версии: [руководство Word](../output/documents/cyber102-product-guide.docx) и [сценарий Word](../output/documents/cyber102-checkpoint-1.docx).

- [Задачи на два дня](TASKS.md) и [чек-лист проверки](CHECKLIST.md).
- [Осмотр камерой](../web/index.html) и [обработка кадра](../web/app.js). [Диагностика насоса](../web/checkpoint.html) и [паспорт, сценарии, журнал](../web/checkpoint.js).
- [Сервер и HTTP API](../server/app.py), [журнал SQLite](../server/history.py), [схема БД](../docs/architecture/database.md) и [контракт зрения](../docs/architecture/vision.md).
- [Сценарий первого чекпоинта](../docs/checkpoints/checkpoint-1.md), [развёртывание](../docs/operations/deployment.md) и [план приёмки](../docs/research/chapters/acceptance-and-roadmap.md).

На [стенде команды](https://cyber102.94.41.87.102.sslip.io/) доступны демонстрационный паспорт `P-102`, сценарии и журнал. Камера анализирует кадр локальной моделью на сервере. Встроенные примеры на экране камеры используют тестовые ответы. [Интеграция паспорта, кадра, датчиков и записи осмотра](../docs/research/chapters/integration-contract.md) запланирована следующим этапом.
