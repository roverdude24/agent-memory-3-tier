# Hindsight API launcher for Windows (run at logon by the HindsightAPI scheduled task).
# HINDSIGHT_API_LLM_API_KEY must be set as a User environment variable; it is never stored in this file.
$env:HINDSIGHT_API_LLM_API_KEY = [Environment]::GetEnvironmentVariable("HINDSIGHT_API_LLM_API_KEY", "User")

# Any OpenAI-compatible endpoint works. Pick a model you have verified with a real inference call.
$env:HINDSIGHT_API_LLM_PROVIDER = "openai"
$env:HINDSIGHT_API_LLM_MODEL = "<model-id>"
$env:HINDSIGHT_API_LLM_BASE_URL = "<https://your-provider/v1>"
Remove-Item Env:OPENAI_BASE_URL -ErrorAction SilentlyContinue  # keeps a global override from hijacking extraction calls

& "$HOME\.hindsight-venv\Scripts\hindsight-api.exe" --host 127.0.0.1 --port 8888 *>> "$HOME\.hindsight-api.log"
