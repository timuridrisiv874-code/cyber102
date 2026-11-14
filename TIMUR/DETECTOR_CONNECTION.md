# Подключение обученного детектора команды

Подготовлены адаптер `server/detector.py` и отдельный worker `vision_runtime/detector_worker.py`. Обученных весов в этом изменении нет. Рабочий InternVL3 остаётся активным до приёмки детектора. Новый worker запускается на Windows-сервере в `C:\cyber102`, в отдельном окружении `.venv-detector`, на RTX 3090 Ti 24 ГБ.

## Что передать от коллеги

Папка `C:\cyber102\models\team-detector\`:

- `best.pt` — доверенный файл detection-модели Ultralytics, полученный непосредственно от обучавшего коллеги.
- `detector-manifest.json` — версия модели, SHA-256 весов и точная карта ID классов. Пример генерации ниже.
- `requirements-lock.txt` и `wheelhouse\` — согласованные версии Ultralytics, PyTorch/CUDA, torchvision, Pillow и зависимостей для **Windows и выбранной версии Python**. Linux-колёса для Windows не подходят. Версии должны совпадать с проверенным окружением экспорта/инференса; не обновлять существующие окружения сервиса.
- `validation.json` — число изображений, разделение train/validation/test, precision/recall/mAP по классам, порог score, условия измерения скорости. Не выдавать score детектора за вероятность правильного диагноза.
- `examples\` — положительные и отрицательные JPEG с ожидаемыми классами и разметкой. Нужны примеры грязи/теней без дефекта, неоднозначный кадр и кадр без оборудования.

Поддерживаются визуальные классы `corrosion`, `wet_patch`, `visible_damage`. Внутренний износ, кавитация и исправность не являются классами этого адаптера. Если модель обучена на иных классах, сначала согласовать их смысл; нельзя автоматически переименовать их в ближайший поддерживаемый дефект.

`component` — только `pump`, `motor`, `coupling`, `pipe`, `bearing`, `valve`, `terminal` либо `unknown`. Для универсального класса ржавчины на разных объектах оставлять `unknown`: тип узла нельзя придумать из самого класса дефекта. Карта должна покрывать **все** классы `model.names` и точно совпадать с их ID и исходными именами.

## Manifest

Команда PowerShell создаёт manifest по локальным весам. **Перед запуском заменить `class_map` реальными ID и именами из модели.** Здесь только пример для модели с тремя указанными классами. `minimumScore=0.4` — пример рабочего порога, его нужно выбрать по validation-набору.

```powershell
Set-Location C:\cyber102
@'
import hashlib, json
from pathlib import Path
folder = Path(r"C:\cyber102\models\team-detector")
digest = hashlib.sha256()
with (folder / "best.pt").open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
class_map = {
    "0": {"sourceName": "corrosion", "class": "corrosion", "component": "unknown"},
    "1": {"sourceName": "wet_patch", "class": "wet_patch", "component": "unknown"},
    "2": {"sourceName": "visible_damage", "class": "visible_damage", "component": "unknown"},
}
manifest = {
    "schemaVersion": 1, "model": "team-detector", "modelVersion": "team-20261113-v1",
    "weightsSha256": digest.hexdigest(), "classMap": class_map,
    "minimumScore": 0.4, "imageSize": 640,
}
(folder / "detector-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
'@ | py -3.11 -
```

`modelVersion` менять при смене весов или настроек модели. Допускаются латинские буквы, цифры и `._+-`, длина до 80 символов. `imageSize`: 320–1280, кратно 32. SHA-256 проверяет целостность; доверие к автору файла он не заменяет. Флаг `--trusted-local-weights` явно подтверждает, что `.pt` получен от доверенного коллеги: такой формат загружается через Python/PyTorch и может содержать исполняемый код.

Необязательное поле `classMinimumScores` задаёт отдельные пороги по исходным ID из `classMap`. Например, `{"0": 0.50, "2": 0.55}`. Для остальных ID действует `minimumScore`. Пороги должны быть больше нуля и не выше единицы. Рабочие значения подбираются заново на валидационной выборке при смене весов. Фактически применённая карта возвращается в `/health`.

## Окружение и запуск на сервере

Пример использует Python 3.11; пакет колёс должен быть собран именно под него. Установка выполняется один раз из заранее подготовленного `wheelhouse`. Эти команды пока не выполнялись: весов и пакета зависимостей ещё нет.

```powershell
Set-Location C:\cyber102
py -3.11 -m venv .venv-detector
.\.venv-detector\Scripts\python.exe -m pip install --no-index --find-links .\models\team-detector\wheelhouse -r .\models\team-detector\requirements-lock.txt
.\.venv-detector\Scripts\python.exe -m vision_runtime.detector_worker --weights C:\cyber102\models\team-detector\best.pt --manifest C:\cyber102\models\team-detector\detector-manifest.json --trusted-local-weights --device cuda:0
```

Для CPU-проверки заменить устройство на `--device cpu`. При запрошенном CUDA worker не переключается на CPU молча. Для показа окно worker оставить открытым; постоянная задача планировщика для него пока не создаётся. Существующие `.venv`, `.venv-internvl` и worker на порту 11436 сохраняют свои настройки.

Worker сначала сверяет hash и классы, загружает модель и выполняет прогрев на синтетическом изображении. Только затем появляется `ready=true` и открывается `127.0.0.1:11437`. Он не принимает URL изображений или имена моделей из запросов, не скачивает веса, отключает автоустановку зависимостей и внешние Python-соединения. Для промышленного контура дополнительно применяются правила сети/ОС; Python-проверка не заменяет сетевую изоляцию.

## Проверка перед переключением вебки

Во **втором** PowerShell на сервере:

```powershell
Invoke-RestMethod http://127.0.0.1:11437/health
$Payload = @{ image = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\cyber102\models\team-detector\examples\positive.jpg')) } | ConvertTo-Json -Compress
Invoke-RestMethod http://127.0.0.1:11437/analyze -Method Post -ContentType 'application/json' -Body $Payload
```

Проверить `ready`, `modelVersion`, `weightsSha256`; прогнать положительные и отрицательные примеры; сверить рамки с разметкой. Порт worker предназначен только для серверного API и не публикуется в Интернет. Телефон обращается к существующей HTTPS-вебке, а не к `11437`.

Контракт ответа:

```json
{
  "schemaVersion": 1,
  "model": "team-detector",
  "modelVersion": "team-20261113-v1",
  "detections": [
    {
      "class": "corrosion",
      "score": 0.87,
      "component": "unknown",
      "box": {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.6}
    }
  ]
}
```

Не более 64 рамок. Координаты нормализованы относительно исходного кадра: `x,y` — **левый верхний угол**, ширина/высота положительны, рамка внутри изображения. Worker использует `boxes.xyxyn`, затем вычисляет ширину/высоту; `xywhn` с координатами центра напрямую в браузер не передаётся. Это соответствует [официальному API Ultralytics Boxes](https://docs.ultralytics.com/modes/predict/#boxes).

Адаптер выбирает карточку по наибольшему score и сохраняет все рамки в `detections`. Пустой список означает `status=unusable`, `reason=no_supported_detections`; он **не означает исправность**. Поддержан только профиль `standard`. При занятости worker отвечает 503 с `code=vision_busy`; другая ошибка 503 не считается очередью.

После приёмки результатов переключить backend и перезапустить только веб-задачу:

```powershell
Set-Location C:\cyber102
Set-Content -Path .\var\vision-backend.txt -Value detector -Encoding ascii -NoNewline
Stop-ScheduledTask -TaskName Cyber102Web
Start-ScheduledTask -TaskName Cyber102Web
```

Откат: записать `internvl3` вместо `detector` и повторить перезапуск `Cyber102Web`. Детекторный worker можно остановить `Ctrl+C`. Если worker отсутствует, детекторный режим сообщает об ошибке; скрытой подмены тестовым ответом или другой моделью нет.

На телефоне: загрузить контрольное фото и короткое видео, проверить совпадение рамки с дефектом, сохранение анализа и отсутствие диагноза «исправен» на пустом результате. На текущем удалённом стенде кадр идёт с телефона на наш сервер; при переносе сервера в локальную сеть приложения используется тот же API.

## Атрибуция и лицензия

Ultralytics — сторонний runtime, а не разработанная нами архитектура. В материалах указывать нашу работу отдельно: датасет/разметка, обучение или дообучение, проверка качества, интеграция с телеметрией и объяснимыми правилами. Сохранить сведения о версиях, лицензиях зависимостей и источниках данных. Ultralytics предлагает AGPL-3.0 и Enterprise; перед закрытой поставкой согласовать выбранные условия, не считать приватный Git-репозиторий исключением из лицензии. [Официальные условия Ultralytics](https://www.ultralytics.com/license).

Контрактные тесты не требуют CUDA/Ultralytics: `python -m unittest tests.test_detector -v`. Они проверяют форматы, ограничения, тип busy, SHA-256, карту классов, преобразование рамок и dispatch через `vision.analyze`. Точность и реальная скорость обученной модели подтверждаются отдельно на переданных весах.
