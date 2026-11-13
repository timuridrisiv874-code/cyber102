# Обучение моделей диагностики насосов

CatBoost обучает отдельный двоичный классификатор для каждого выбранного отказа. AutoGluon Tabular позволяет сравнить CatBoost и случайный лес на том же разбиении. Один осмотр может содержать несколько подтверждённых отказов. Модели обучаются локально на размеченных наблюдениях, а их отчёт предназначен для оценки командой. Диагностика первого чекпоинта продолжает работать по 12 правилам и демонстрационным сценариям. Наработка по регламенту и критические предупреждения остаются правилами. Для этой задачи ещё нужна размеченная заводская выборка. CatBoost обучен в отдельном эксперименте на лабораторном наборе UCI. [Эксперимент на стенде UCI](../docs/benchmark-uci.md)

## Формат данных

Одна строка CSV или JSONL описывает один момент наблюдения по одному насосу. Обязательны `serial` (постоянный идентификатор насоса), `timestamp` (ISO 8601 с часовым поясом) и `source=field` (фактические замеры). Повторы пары «серийный номер и время» после приведения к UTC отклоняются. Строки со сценарием первого чекпоинта или сгенерированными данными не используют для обучения и оценки.

| Метка | Положительный случай |
| --- | --- |
| `bearing_fault` | Подтверждённый дефект смазки или механический износ подшипника |
| `cavitation` | Подтверждённая кавитация |
| `shaft_misalignment` | Подтверждённая расцентровка валов |
| `hydraulic_wear` | Подтверждённый износ проточной части |
| `foundation_looseness` | Подтверждённое ослабление крепления |

Используйте `1` при подтверждении осмотром или ремонтом, `0` при подтверждённом отсутствии отказа и пустую ячейку или `null`, когда состояние неизвестно. Источник разметки и дату подтверждения храните отдельно. Метки, вычисленные теми же 12 правилами из входных признаков, воспроизводят правила и не измеряют диагностическую пользу модели.

| Измерение | Допустимые поля |
| --- | --- |
| Виброскорость, мм/с RMS | `vibration_rms_mm_s`, `broadband_vibration_rms_mm_s`, `axial_vibration_rms_mm_s`, `vibration_1x_mm_s` |
| Высокочастотное виброускорение, g | `vibration_high_frequency_g` |
| Температура и превышение базовой, °C | `bearing_temperature_c`, `bearing_temperature_delta_c` |
| Ток, А, и дисбаланс, % | `motor_current_a`, `phase_imbalance_pct` |
| Давление, МПа | `inlet_pressure_mpa`, `discharge_pressure_mpa` |
| Расход, м³/ч | `flow_m3_h` |
| КПД и относительное снижение от базы, % | `efficiency_pct`, `efficiency_drop_pct` |
| Моточасы и скорость вращения, об/мин | `hours`, `shaft_speed_rpm` |

Измерения должны быть конечными числами. Пустые ячейки и `null` остаются пропусками. Для строки нужно хотя бы одно измерение. Укажите единицы и метод расчёта вибрации, давления и КПД при подготовке выгрузки. Признаки рассчитывайте только по данным, доступным к `timestamp`, в сопоставимом режиме нагрузки. Результаты ремонта и диагностики не входят в признаки. Серийный номер, время, происхождение и целевые метки используются только для отбора строк и оценки.

Минимальный заголовок CSV для одного отказа может выглядеть так:

```csv
serial,timestamp,source,vibration_rms_mm_s,bearing_temperature_c,motor_current_a,bearing_fault
```

## Разбиение и оценка

Запишите список насосов и даты в `split.json`. Идентификаторы и даты ниже служат примером структуры:

```json
{
  "train": ["P-001", "P-002"],
  "validation": ["P-003", "P-004"],
  "test": ["P-005", "P-006"],
  "validation_from": "2026-07-01T00:00:00Z",
  "test_from": "2026-09-01T00:00:00Z"
}
```

Серийные номера трёх частей должны различаться. Обучение берёт строки строго до первой даты, валидация берёт строки от первой даты до второй, тест берёт строки от второй даты включительно. Строки вне назначенного окна или без назначенной части исключаются и учитываются в отчёте. Для каждой выбранной метки нужны положительные и отрицательные примеры в каждой части. При нехватке данных для части отказов укажите только проверяемые метки через `--labels`.

Валидационная выборка используется для остановки CatBoost или выбора модели AutoGluon. Тестовая применяется один раз после выбора модели. Отчёт содержит состав выборок, average precision, долю положительных, матрицу ошибок, precision, recall, specificity, F1 и balanced accuracy при пороге 0,5. Порог служит для сравнения моделей. Для эксплуатации его отдельно согласуют по риску пропусков и ложных срабатываний. Малое число насосов и подтверждённых отказов ограничивает применимость результата.

## Запуск на сервере с RTX 3090

Команды обучения используют отдельное окружение на Python 3.12. Если интерпретатор ещё отсутствует на сервере, установите его параллельно с Python 3.11 приложения. Схему и разбиение можно проверить в имеющемся Python без ML-библиотек:

```sh
python -m ml.train validate --data var/observations.csv --split var/split.json --labels bearing_fault
```

В Windows PowerShell:

```powershell
nvidia-smi
py -3.12 -m venv .venv-ml
.\.venv-ml\Scripts\python.exe -m pip install -r ml/requirements-catboost.txt
.\.venv-ml\Scripts\python.exe -m ml.train train --data var/observations.csv --split var/split.json --labels bearing_fault --engine catboost --gpu --output var/model-catboost
```

В Linux или WSL с доступным GPU:

```sh
nvidia-smi
python3.12 -m venv .venv-ml
.venv-ml/bin/python -m pip install -r ml/requirements-catboost.txt
.venv-ml/bin/python -m ml.train train --data var/observations.csv --split var/split.json --labels bearing_fault --engine catboost --gpu --output var/model-catboost
```

Для CPU уберите `--gpu`. CatBoost принимает номер первой видимой GPU и фиксированный seed. Обучение на GPU может давать небольшие различия между запусками. [Документация CatBoost о GPU](https://catboost.ai/docs/en/features/training-on-gpu)

Для сравнения на Linux или WSL создайте `.venv-automl`. В Windows используйте `py -3.12 -m venv .venv-automl`, а затем `.\.venv-automl\Scripts\python.exe` в командах установки и запуска.

```sh
python3.12 -m venv .venv-automl
.venv-automl/bin/python -m pip install -r ml/requirements-autogluon.txt
.venv-automl/bin/python -m ml.train train --data var/observations.csv --split var/split.json --labels bearing_fault --engine autogluon --time-limit 300 --output var/model-autogluon
```

Команда AutoGluon использует CPU по умолчанию. Валидация передаётся явно, случайное разбиение для бэггинга и стекинга отключено. Для GPU поддержку выбранных моделей и драйвера следует проверить на целевом сервере. [Установка AutoGluon Tabular](https://auto.gluon.ai/stable/install.html) · [Параметры обучения](https://auto.gluon.ai/stable/api/autogluon.tabular.TabularPredictor.fit.html)

Результат сохраняется в новую папку с `report.json`, хешами входных файлов, версиями, списком признаков и артефактами моделей. После оценки AutoGluon удаляет из артефактов кэш обучающей и валидационной выборок. Храните каталог моделей в доверенном контуре и загружайте только собственные артефакты с совместимой версией пакета. [Очистка кэша AutoGluon](https://auto.gluon.ai/stable/api/autogluon.tabular.TabularPredictor.save_space.html) · [Загрузка моделей AutoGluon](https://auto.gluon.ai/stable/api/autogluon.tabular.TabularPredictor.load.html)
