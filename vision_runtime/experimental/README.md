# Эксперимент InternVL3 GGUF

Этот тест оценивает отдельный локальный кандидат. Результаты сохранены в [отчёте](../../docs/ml/gguf-experiment-report.json). На проверочном наборе быстрый Q4-вариант выдавал ложную коррозию на белом кадре и рисунке человека. Консервативная подсказка улучшила отказ от оценки, однако давала рамки на всё изображение и нулевую рамку при ответе о видимом насосе.

## Повторение теста

1. Скачайте два файла зафиксированного GGUF из [репозитория ggml-org](https://huggingface.co/ggml-org/InternVL3-2B-Instruct-GGUF/tree/f763aa88f2ad2dbd5246350660fec7d50a20a7b0) и два CUDA-архива из [релиза llama.cpp b12345](https://github.com/ggml-org/llama.cpp/releases/tag/b12345). Сверьте SHA-256 из [конфигурации](gguf-config.json). Распакуйте архивы в одну папку.
2. Запустите отдельный CUDA `llama-server.exe` с весами `-m InternVL3-2B-Instruct-Q4_K_M.gguf`, проектором `--mmproj mmproj-InternVL3-2B-Instruct-f16.gguf` и флагами `runtime_flags` из конфигурации. Для `--api-key` задайте отдельный тестовый ключ и передайте его клиенту через `CYBER102_GGUF_API_KEY`. Тест обращается к `127.0.0.1:11437`.
3. Из корня Cyber102 в окружении с Pillow выполните команды ниже. Для `--warman` используйте локальную копию фотографии Bernard S. Janse с хешем из конфигурации. Изображение доступно по CC BY 2.5. [Фото Warman](https://commons.wikimedia.org/wiki/File:Warman_centrifugal_pump.jpg)

```powershell
python -m vision_runtime.experimental.gguf_benchmark --fixtures var/gguf --generate-fixtures --runs 2
python -m vision_runtime.experimental.gguf_benchmark --fixtures var/gguf --variant unconstrained
python -m vision_runtime.experimental.gguf_benchmark --fixtures var/gguf --variant conservative
python -m vision_runtime.experimental.gguf_benchmark --fixtures var/gguf --expanded
python -m vision_runtime.experimental.gguf_benchmark --fixtures var/gguf --expanded --variant conservative
```

Отчёт содержит задержки локальных HTTP-вызовов без подготовки JPEG и без передачи по Wi‑Fi. Для повторения использовались исходный промпт проекта и тот же валидатор приложения. Метка `clean` идентифицирует файл `demo-pump-clean.jpg`. Состояние его поверхности требует отдельной разметки. Синтетические отрицательные кадры проверяют отказ от оценки. Фотографии насосов в этом прогоне служат проверкой поведения и формата.
