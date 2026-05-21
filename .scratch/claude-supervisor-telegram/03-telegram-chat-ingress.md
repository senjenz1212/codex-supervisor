# Issue 03: Route Telegram Free Text Into Claude Supervisor Turns

## What to build

Telegram messages that are not slash commands should create a supervisor turn,
invoke the Claude supervisor runtime with tool access, persist the turn, and
send the response back to Telegram.

## PRD Promise

**Promise IDs:** CS1

**Public boundary:** `telegram_chat_ingress`

**Allowed outcomes:** authorized free text invokes supervisor runtime; response
is sent to Telegram; failures degrade with a short message; slash commands keep
working.

**Forbidden outcomes:** free-form Telegram text is ignored; unauthorized chats
trigger Claude; raw secrets are stored or returned; a model failure crashes the
poller.

## TDD Plan

RED: Feed a fake Telegram update with "what is happening in Vela chat bot?" to
the poller and assert the chat handler receives it and a Telegram response is
posted.

GREEN: Add `TelegramChatSupervisor` and `TelegramPoller` chat routing.

## Status

Implemented. Covered by `tests/test_telegram_chat_ingress.py`.
