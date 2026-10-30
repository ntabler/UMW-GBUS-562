# Week 2: Prompt Lab

[prompt-lab.html](prompt-lab.html) is a single-page web app for testing prompts against Claude (`claude-sonnet-5`) through the Anthropic API. It is plain HTML and JavaScript with no server: open the file in a browser and start typing.

## How to use it

1. Paste your Anthropic API key into the key field. The key stays in the page's memory only. It is never saved or written to any file, so you paste it again each time you open the page.
2. Optionally enter a system prompt. It stays in place after each send. If you change it partway through a conversation, the log notes the change.
3. Type a prompt and press **Send** (or Ctrl/⌘ + Enter). The prompt box clears after each send.
4. The conversation log labels each message **System**, **You**, or **Claude**. Every prompt is sent together with the whole conversation so far, so the log shows exactly what Claude sees.
5. **New Conversation** clears the log and starts fresh.
6. **Export** downloads the conversation as a Markdown file: the model name on the first line, then each message under a `### System`, `### You`, or `### Claude` heading.

Errors (a bad key, a network problem, a refusal) appear in the log. A prompt that fails is greyed out, is not sent with later prompts, and goes back into the prompt box so you can try again.
