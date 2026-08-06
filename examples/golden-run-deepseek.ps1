$ErrorActionPreference = "Stop"

# Set DEEPSEEK_API_KEY in the environment before running.
npm run dev -w @veridix/cli -- run golden `
  --endpoint https://api.deepseek.com `
  --model deepseek-v4-flash `
  --target https://lab.example.test `
  --api-key-ref env:DEEPSEEK_API_KEY `
  --thinking-mode enabled `
  --max-turns 5 `
  --json
