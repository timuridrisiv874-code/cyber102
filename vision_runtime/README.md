# Локальный процесс InternVL3

GPU-процесс Cyber102 обслуживает запросы веб-API на `127.0.0.1:11436`. На Windows 11 с RTX 3090 Ti выбран `OpenGVLab/InternVL3-2B-hf`, ревизия `cb57a075cb75a2e6d1b668b128d48bb00ae321d2`. Модель использует нативный Transformers, BF16 и SDPA. При недоступной CUDA запуск завершается ошибкой. [Карточка модели](https://huggingface.co/OpenGVLab/InternVL3-2B-hf) · [Документация Transformers](https://huggingface.co/docs/transformers/en/model_doc/internvl)

## Подготовка Windows

В `C:\cyber102` требуется отдельное Python 3.11 окружение `.venv-internvl`. Используются `torch==2.8.0+cu128`, `torchvision==0.23.0+cu128` и `transformers==4.57.1`. CUDA-колёса ставятся первыми из индекса PyTorch или подготовленного wheelhouse. Остальные зависимости перечислены в [requirements.txt](requirements.txt). [Сборки PyTorch для CUDA](https://pytorch.org/get-started/previous-versions/)

Для установки из подготовленного wheelhouse используйте PowerShell в каталоге проекта. Wheelhouse должен содержать указанные CUDA-колёса и зависимости Windows x64 для Python 3.11.

```powershell
Set-Location C:\cyber102
.\runtime\python.exe -m venv .venv-internvl
.\.venv-internvl\Scripts\python.exe -m pip install --no-index --find-links wheelhouse `
  'torch==2.8.0+cu128' 'torchvision==0.23.0+cu128'
.\.venv-internvl\Scripts\python.exe -m pip install --no-index --find-links wheelhouse `
  -r vision_runtime\requirements.txt
.\.venv-internvl\Scripts\python.exe -m pip check
```

На машине с доступом к Hub запустите загрузку зафиксированной ревизии. Веса, настройки и манифест передайте в `C:\cyber102\models\internvl3-2b-hf`. Загрузчик сверяет ревизию, хеши файлов с метаданными Hub и записывает SHA-256 в `cyber102-manifest.json`.

```powershell
python -m vision_runtime.download --directory models/internvl3-2b-hf
```

При инференсе процесс читает локальные safetensors и настройки через `local_files_only=True` и `trust_remote_code=False`. Включены офлайн-переменные Hugging Face и Python audit hook, отклоняющий внешние DNS-запросы и соединения процесса. Этот hook действует на Python-операции. На действующем стенде дополнительно включены два правила Windows Firewall: исходящие соединения запрещены для `.venv-internvl\Scripts\python.exe` и частного `runtime\python.exe`. Скрипт [lock-inference-network.ps1](../deploy/lock-inference-network.ps1) ограничен этими процессами. Проверка внешнего HTTPS завершилась `WinError 10013`, а анализ через loopback продолжил работать. Для обновления весов используют отдельный загрузчик вне изолированного runtime и передают проверенные файлы. Это изоляция процессов модели и приложения; HTTPS от телефона к удалённому серверу остаётся сетевой передачей. [Офлайн-режим Hub](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables#hfhuboffline)

## Запуск и проверка

После установки окружения и весов в PowerShell администратора запустите `C:\cyber102\deploy\install-internvl.ps1`. Он регистрирует `Cyber102InternVL` для старта Windows под `fatik` и перезапуска при сбое. Сначала убедитесь, что процесс модели готов.

```powershell
Get-ScheduledTask -TaskName Cyber102InternVL | Select-Object TaskName,State
Invoke-RestMethod http://127.0.0.1:11436/health
Get-Content C:\cyber102\var\internvl-worker.log -Tail 20
nvidia-smi
```

`/health` сообщает идентификатор и ревизию, CUDA-устройство, готовность и занятость модели. Для выбора процесса веб-приложением установите `internvl3` в `C:\cyber102\var\vision-backend.txt` и перезапустите `Cyber102Web`. Убедитесь, что ответ `/analyze` содержит `model: OpenGVLab/InternVL3-2B-hf`. При необходимости оператор может явно выбрать `ollama` в том же файле и перезапустить веб-приложение. Ошибка выбранной модели возвращается клиенту.

## Интерфейс процесса

Внутренний `POST /analyze` принимает JSON с одним Base64-изображением JPEG или PNG до 3 МиБ. Запрос передаёт только кадр, а инструкции задаёт приложение. Один запрос выполняется на GPU в каждый момент. Сервис валидирует наблюдение, журналирует время и статус без сохранения изображений и возвращает `model`, `observation` и `durationSeconds`. Публичный маршрут веб-приложения сохраняет прежний формат multipart. [Архитектура камеры](../docs/architecture/vision.md)

Ограничение `max_time=85` в Transformers проверяется между шагами генерации и не является жёстким пределом всего GPU-вызова. Отмена запроса в браузере не прерывает текущий GPU-вызов. Второй вызов отклоняется, пока первый не завершится. Версии установленного Windows-окружения записаны в [requirements-lock-windows.txt](requirements-lock-windows.txt).
