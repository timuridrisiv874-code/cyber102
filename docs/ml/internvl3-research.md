# InternVL3 на сервере Cyber102

Для Cyber102 на Windows 11 и RTX 3090 Ti 24 ГБ выбран **OpenGVLab/InternVL3-2B-hf** в BF16 с одним одновременно выполняющимся запросом. Он использует нативный Transformers и отдельное окружение. GGUF через llama.cpp остаётся вариантом для отдельного эксперимента. [Конфигурация GPU-процесса](../../vision_runtime/README.md)

Исходный **OpenGVLab/InternVL3-2B** содержит 2,09 млрд параметров и 4,19 ГБ файлов в опубликованном репозитории. **InternVL3-8B** содержит 7,94 млрд параметров, репозиторий занимает 15,9 ГБ. Для 8B отдельно измеряют пик VRAM на одном кадре и на последовательности. У опубликованной серии InternVL3.0 нет 4B. Версия с таким названием относится к InternVL3.5 и содержит 4,7 млрд параметров. Число параметров и объём файлов позволяют оценить размер модели, а задержку и качество на насосах нужно измерить рядом с исходным Qwen3-VL 8B в Ollama. [Релиз InternVL3.0](https://internvl.github.io/blog/2025-04-11-InternVL-3.0/) · [Файлы 2B](https://huggingface.co/OpenGVLab/InternVL3-2B/tree/main) · [Файлы 8B](https://huggingface.co/OpenGVLab/InternVL3-8B/tree/main) · [Релизы InternVL3.5](https://github.com/OpenGVLab/InternVL#model-zoo)

## Ревизии и загрузчики

| Пакет OpenGVLab | Зафиксированная ревизия | Загрузчик |
| --- | --- | --- |
| InternVL3-2B | [899155015275a9b7338c7f4677e19c784e0e5a21](https://huggingface.co/OpenGVLab/InternVL3-2B/tree/899155015275a9b7338c7f4677e19c784e0e5a21) | `AutoModel`, `AutoTokenizer`, `model.chat` |
| InternVL3-8B | [853e3a797a661694b1b8ece0cb72dc2b23e3dac9](https://huggingface.co/OpenGVLab/InternVL3-8B/tree/853e3a797a661694b1b8ece0cb72dc2b23e3dac9) | Та же реализация `model.chat` |
| InternVL3-2B-hf | [cb57a075cb75a2e6d1b668b128d48bb00ae321d2](https://huggingface.co/OpenGVLab/InternVL3-2B-hf/tree/cb57a075cb75a2e6d1b668b128d48bb00ae321d2) | Нативные `AutoProcessor`, `InternVLForConditionalGeneration`, `generate` |
| InternVL3-8B-hf | [259a3b64a14623c0ec91a045cb43f7c5af5fa6af](https://huggingface.co/OpenGVLab/InternVL3-8B-hf/tree/259a3b64a14623c0ec91a045cb43f7c5af5fa6af) | Та же нативная реализация |

Для исходных весов карточки помечены Apache-2.0, а лицензия кода основного GitHub-репозитория OpenGVLab — MIT. Для конвертации `-hf` сохранённый манифест отмечает `license_metadata: other`; карточки моделей отсылают к условиям Qwen. При использовании конвертации фиксируют её карточку и отдельно проверяют применимые к выбранным весам условия распространения. [Карточка 2B](https://huggingface.co/OpenGVLab/InternVL3-2B) · [Карточка 8B](https://huggingface.co/OpenGVLab/InternVL3-8B) · [Лицензия кода](https://github.com/OpenGVLab/InternVL/blob/main/LICENSE) · [Карточка 2B-hf](https://huggingface.co/OpenGVLab/InternVL3-2B-hf) · [Карточка 8B-hf](https://huggingface.co/OpenGVLab/InternVL3-8B-hf)

Для выбранного варианта `-hf` используется Python 3.11, PyTorch 2.8.0 и Torchvision 0.23.0 с CUDA 12.8, Transformers 4.57.1, BF16 и SDPA. Нативная поддержка InternVL появилась в Transformers 4.52.1. Исходные пакеты OpenGVLab с методом `model.chat` используют отдельный загрузчик с `trust_remote_code=True`, а их карточки приводят нижнюю границу Transformers 4.37.2. Код исходного загрузчика поддерживает eager attention при `use_flash_attn=False`. [PyTorch на Windows](https://pytorch.org/get-started/locally/) · [Документация OpenGVLab](https://internvl.readthedocs.io/en/latest/internvl3.0/quick_start.html) · [Код загрузчика](https://huggingface.co/OpenGVLab/InternVL3-2B/blob/899155015275a9b7338c7f4677e19c784e0e5a21/modeling_internvl_chat.py) · [FlashAttention](https://github.com/Dao-AILab/flash-attention#installation-and-features) · [Transformers 4.52.1](https://github.com/huggingface/transformers/releases/tag/v4.52.1) · [Нативная карточка](https://huggingface.co/OpenGVLab/InternVL3-2B-hf/blob/main/README.md)

Загрузку проводят отдельно от сервиса анализа, с полным хешем коммита. Например, на машине с доступом к Hub скачивают снимок в папку для переноса.

```powershell
python -m vision_runtime.download --directory models/internvl3-2b-hf
```

Загрузчик Cyber102 сверяет ревизию и хеши снимка до запуска. При инференсе выбранный нативный загрузчик использует локальный каталог с `trust_remote_code=False`, `local_files_only=True`, `HF_HUB_OFFLINE=1` и весами safetensors. Для исследований с исходным `model.chat` флаг `trust_remote_code=True` разрешает исполнять Python из снимка, поэтому сначала проверяют код зафиксированной ревизии. [Загрузка по ревизии Hugging Face](https://huggingface.co/docs/huggingface_hub/en/guides/download#download-an-entire-repository) · [Пользовательские модели Transformers](https://huggingface.co/docs/transformers/en/models#custom-models)

## Альтернативный вариант GGUF

Пакет `ggml-org/InternVL3-2B-Instruct-GGUF` помечен Apache-2.0 и ссылается на `OpenGVLab/InternVL3-2B-Instruct`. В ревизии `f763aa88f2ad2dbd5246350660fec7d50a20a7b0` есть языковые веса `InternVL3-2B-Instruct-Q4_K_M.gguf` (1,12 ГБ) и визуальный проектор `mmproj-InternVL3-2B-Instruct-f16.gguf` (628 МБ). Для локального прогона оба файла передают готовому Windows CUDA `llama-server` аргументами `-m` и `--mmproj`. Для отдельного эксперимента JPEG можно передать на loopback `/v1/chat/completions` в виде data URI. llama.cpp поддерживает передачу нескольких изображений и ограничение JSON по схеме. На установленной версии отдельно проверяют загрузку проектора, реальный GPU-offload, понимание двух разных кадров и ограничение ответа схемой. [Зафиксированный пакет GGUF](https://huggingface.co/ggml-org/InternVL3-2B-Instruct-GGUF/tree/f763aa88f2ad2dbd5246350660fec7d50a20a7b0) · [Поддержка мультимодальных моделей llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/mtmd/README.md#how-to-obtain-mmproj) · [Локальный API llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Анализ кадра и возможности видео

В Cyber102 браузер отправляет JPEG в существующий `POST /analyze`. Веб-API нормализует кадр и передаёт его на loopback `127.0.0.1:11436`. GPU-процесс подготавливает вход с `AutoProcessor`, выполняет нативный `generate` и проверяет один JSON с полями `component`, `condition`, `box`, `observation`. Карточка получает рамку относительно проанализированного снимка. [Архитектура камеры](../architecture/vision.md) · [GPU-процесс](../../vision_runtime/README.md)

Видеопроверка Cyber102 выбирает отдельные кадры из живого предпросмотра. Каждый запрос оценивается самостоятельно, следующий отправляется после завершения предыдущего. В Quick Start OpenGVLab показан отдельный способ анализа последовательности. Он выбирает восемь кадров из MP4 через Decord и передаёт их одному вызову `model.chat`. Нативный `InternVLProcessor` тоже принимает тензоры видео и добавляет в промпт маркеры отдельных кадров. Для проверки связи событий между кадрами потребуется собственный набор размеченных клипов. [Видео в Quick Start](https://internvl.readthedocs.io/en/latest/internvl3.0/quick_start.html#video-multi-round-conversation) · [Код InternVLProcessor 4.57.1](https://github.com/huggingface/transformers/blob/v4.57.1/src/transformers/models/internvl/processing_internvl.py)

## Проверка по Wi‑Fi и сравнение

На локальном сервере GPU-процесс слушает loopback. Для iPhone и Android веб-интерфейс и API идут через один HTTPS-адрес с доверенным сертификатом и маршрутом внутри Wi‑Fi или VPN. Публичный адрес демонстрации проверяет доступность стенда, а доступ без интернета проверяют отдельно с локальным DNS и сертификатом, которому доверяют телефоны. Для WebView проверяют разрешение Android и выдачу `RESOURCE_VIDEO_CAPTURE` только доверенному origin. Физические телефоны нужны для приёмки камеры, ориентации кадров и поведения при отключении Wi‑Fi. [Доступ к камере в W3C](https://www.w3.org/TR/mediacapture-streams/#dom-mediadevices-getusermedia) · [Разрешения WebView](https://developer.android.com/reference/android/webkit/PermissionRequest) · [Проверка локального Wi‑Fi](../operations/local-network.md)

На одних и тех же размеченных фото и клипах сравнивают Qwen3-VL 8B и InternVL3-2B, затем при необходимости InternVL3-8B. Фиксируют ревизию модели, формат весов, число кадров, пик VRAM, задержку после запуска и после прогрева, долю валидных JSON, ошибки класса и рамки, ложные тревоги и долю отказов на нерелевантных кадрах. Вес модели, тестовая задержка, качество распознавания и допуск к эксплуатации отражают разные свойства. Доступные публичные результаты OpenGVLab относятся к общим бенчмаркам. Первые функциональные измерения на RTX 3090 Ti приведены ниже. Точность на размеченных насосах пока не измерена.

## Фактический прогон 13 ноября 2026 года

Пять входов прогнаны дважды: два синтетических изображения насоса из вебки, открытое фото Warman без разметки дефектов, белый кадр и изображение с текстом. Фото `demo-pump-clean.jpg` содержит сколы и коричневые участки; имя файла и тестовый ответ интерфейса не являются экспертной меткой. На всех трёх насосах обе модели дали предварительный признак коррозии; отрицательные кадры получили `unusable`.

| Конфигурация | Валидные ответы | Медиана второго прохода | Диапазон второго прохода |
| --- | --- | --- | --- |
| InternVL3-2B-hf, BF16, Transformers 4.57.1 | 10/10 | 4,391 с | 2,469–5,094 с |
| Qwen3-VL 8B, Ollama | 10/10 | 0,766 с | 0,687–0,907 с |

[InternVL: все результаты](internvl3-smoke-report.json) · [Qwen: все результаты](qwen-comparison-report.json) · [Код прогона](../../vision_runtime/benchmark.py)

Это сравнение двух установленных конфигураций, с разными загрузчиками, инструкциями, квантованием и поведением кеша. Оно не доказывает преимущество одной модели по качеству или вычислительной эффективности. Первая загрузка Qwen включена только в первый проход (12,594 с). InternVL загружена до начала замера. У InternVL рамки часто грубые, формулировки требуют улучшения. Промпт со встроенной JSON Schema первоначально приводил к неверному формату; компактная инструкция с четырьмя полями устранила проблему в этом небольшом прогоне.

Через публичный HTTPS реальные ответы InternVL получили Chromium (3 кадра, последняя задержка 4 с) и WebKit в мобильном профиле (1 кадр, 5,2 с). В обоих случаях поток камеры синтетический, модель на GPU настоящая; проверены отдельный живой предпросмотр, рамка на снимке, отсутствие ошибок JavaScript и горизонтального переполнения. [Протокол браузеров](internvl3-browser-report.json) · [Повторяемый тест](../../tests/browser/live-video.cjs)

На стенде подтверждены готовность CUDA-воркера, автозапуск заданием и отсутствие исходящего HTTPS из изолированного Python (`WinError 10013` при работающем loopback-инференсе). Это не испытание всего хоста без интернета: сервер сейчас удалённый, телефоны передают снимки через HTTPS. Локальная сеть и физические телефоны проверяются отдельно.

[Сводка проверки выпуска](internvl3-validation-report.json) содержит фактически выполненные проверки и оставшиеся пункты приёмки.

## Измеренный кандидат GGUF и решение

Отдельно запущен `InternVL3-2B-Instruct-Q4_K_M` из пакета ggml-org с f16-проектором в официальном CUDA llama.cpp b12345. Это другой checkpoint (`-Instruct`), формат весов и runtime. Два файла модели и два архива CUDA сверены по SHA-256. В базовых десяти вызовах медиана loopback-задержки составила 0,6245 с (0,451–0,926 с); кеш промпта отключён.

Кандидат не выбран для стенда: базовая подсказка дала ложную коррозию на белом кадре и рисунке человека. Консервативная подсказка отклонила четыре отрицательных кадра, но на фото давала рамку на всё изображение либо нулевую рамку при ответе о видимом насосе. Всего сохранены 34 вызова с тремя вариантами инструкций. Дополнительный контроль действующего нативного InternVL3 вернул `unusable` на рисунках человека и мебели (3,298 и 2,214 с). Эти небольшие проверки помогают выбрать рабочую конфигурацию; они не заменяют оценку на заводской разметке.

Экспериментальный процесс на `11437` остановлен, действующий стенд продолжает использовать `OpenGVLab/InternVL3-2B-hf` на `11436`. [Полный отчёт](gguf-experiment-report.json) · [Конфигурация и воспроизведение](../../vision_runtime/experimental/README.md)
