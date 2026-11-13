$env:OLLAMA_HOST='127.0.0.1:11435'
$env:OLLAMA_MODELS='C:\Users\fatik\.ollama\models'
$env:OLLAMA_NO_CLOUD='1'
$env:OLLAMA_NUM_PARALLEL='1'
$env:OLLAMA_MAX_LOADED_MODELS='1'
$env:OLLAMA_KEEP_ALIVE='5m'
& 'C:\Users\fatik\AppData\Local\Programs\Ollama\ollama.exe' serve >> C:\cyber102\var\vision.log 2>&1
exit $LASTEXITCODE
