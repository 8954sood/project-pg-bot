# AGENTS.md

## Project Overview

This repository is a Discord bot project.

The codebase should be organized around two main layers:

```text
UI Layer
Core Layer
```

The goal is to keep Discord-specific code separate from feature logic, while avoiding unnecessary over-engineering.

The repository entrypoint remains:

```text
newapp.py
```

Do not rename or remove `newapp.py` unless explicitly requested.

---

## Target Architecture

```text
project-pg-bot/
  newapp.py

  ui/
    common/
      __init__.py
      views.py

    tts/
      __init__.py
      cog.py
      views.py

    sleep_timer/
      __init__.py
      cog.py
      views.py

    role/
      __init__.py
      cog.py
      views.py

    emoji/
      __init__.py
      cog.py

    cog_manager/
      __init__.py
      cog.py

  core/
    bot/
      __init__.py
      settings.py
      logging.py
      loader.py

    tts/
      __init__.py
      models.py
      service.py
      queue.py
      playback.py
      text_normalizer.py
      engine_selector.py

    sleep_timer/
      __init__.py
      models.py
      service.py
      scheduler.py
      parser.py
      formatter.py

    local/
      __init__.py
      local_core.py
      ...

    tts_engines/
      __init__.py
      base.py
      gtts_engine.py
      ai_stream_engine.py
      stream_source.py
```

---

## Layer Rules

### UI Layer

The `ui/` package contains Discord-facing code.

UI code may use:

* `discord.Interaction`
* `discord.Message`
* `discord.Member`
* `discord.VoiceClient`
* `discord.ui.View`
* `discord.ui.Modal`
* `commands.Cog`
* `app_commands.command`

UI responsibilities:

* Receive Discord events and commands.
* Read user input from Discord objects.
* Call Core services.
* Convert Core results into Discord responses.
* Define Discord Views, Modals, Buttons, and interaction callbacks.

UI code may import Core code.

```python
from core.sleep_timer.service import SleepTimerService
from core.tts.service import TTSService
```

UI code should not contain business logic such as:

* TTS queue policy
* TTS engine selection rules
* Sleep timer reservation rules
* DB persistence rules
* Complex scheduler logic

---

### Core Layer

The `core/` package contains feature logic.

Core responsibilities:

* TTS queue management
* TTS playback flow
* TTS text normalization
* TTS engine selection
* Sleep timer parsing
* Sleep timer reservation management
* Sleep timer scheduling
* DB access
* Bot settings, logging, and loader helpers

Core code should avoid importing UI code.

Do not import from `ui.*` inside `core.*`.

Bad:

```python
from ui.sleep_timer.views import ManagementView
```

Good:

```python
from core.sleep_timer.models import SleepTimerReservation
```

---

## Dependency Rule

Dependencies must flow in one direction:

```text
ui -> core
```

Core must not depend on UI.

Exception:

Some Core modules may need limited Discord API access when the feature cannot work without Discord runtime objects.

Allowed exceptions:

```text
core/tts/playback.py
core/sleep_timer/scheduler.py
```

These files may use Discord runtime objects such as `VoiceClient`, `Guild`, `Member`, or channel APIs if necessary.

Keep Discord dependency isolated inside these modules.

---

## UI Package Rules

UI is organized by feature domain, not by component type.

Use:

```text
ui/tts/cog.py
ui/tts/views.py
ui/sleep_timer/cog.py
ui/sleep_timer/views.py
```

Avoid:

```text
ui/cogs/tts.py
ui/views/sleep_timer.py
```

Each feature UI package should contain:

```text
cog.py      # Discord command/event entrypoint
views.py    # Discord View/Modal/Button classes
```

Optional files may be added when needed:

```text
responses.py
messages.py
```

Do not create extra files prematurely.

---

## Common UI

Shared Discord UI helpers belong in:

```text
ui/common/
```

Examples:

```text
ui/common/views.py
```

This may contain:

* `OwnedLayoutView`
* `status_view`
* common interaction guard helpers
* reusable status/error views

Feature-specific Views should stay inside their feature package.

---

## Core Feature Package Rules

Each Core feature should be organized by responsibility.

For TTS:

```text
core/tts/models.py
core/tts/service.py
core/tts/queue.py
core/tts/playback.py
core/tts/text_normalizer.py
core/tts/engine_selector.py
```

For Sleep Timer:

```text
core/sleep_timer/models.py
core/sleep_timer/service.py
core/sleep_timer/scheduler.py
core/sleep_timer/parser.py
core/sleep_timer/formatter.py
```

Keep files small, but do not split files without a clear reason.

---

## Cog Rules

Cog files should be thin.

A Cog may:

* Register slash commands.
* Register prefix commands.
* Register Discord event listeners.
* Extract IDs and simple input from Discord objects.
* Call Core services.
* Create UI Views.
* Send or edit Discord responses.

A Cog should not:

* Directly manage DB tables.
* Directly manage long-running scheduler tasks.
* Directly implement queue policy.
* Directly implement complex domain rules.
* Contain large Discord View or Modal classes.

---

## View Rules

View files may contain Discord UI classes.

Views may:

* Receive button and modal interactions.
* Validate interaction ownership.
* Call Core services.
* Render service results into Discord messages.

Views should not:

* Directly access DB tables.
* Directly create or cancel scheduler tasks.
* Contain complex business rules.
* Import from other feature UI packages unless the component is truly shared.

Shared View behavior should move to `ui/common/views.py`.

---

## Service Rules

Service classes belong in Core.

Services should contain feature use cases.

Examples:

```python
SleepTimerService.create_pending(...)
SleepTimerService.confirm_pending(...)
SleepTimerService.cancel_reservation(...)

TTSService.handle_message(...)
TTSService.join(...)
TTSService.leave(...)
```

Services should return result objects rather than Discord messages directly.

Prefer dataclasses for result models.

Example:

```python
@dataclass(slots=True)
class CancelReservationResult:
    ok: bool
    message: str
```

---

## Model Rules

Feature models belong in:

```text
core/<feature>/models.py
```

Use dataclasses for internal state and result objects.

Prefer:

```python
@dataclass(slots=True)
class TTSQueueItem:
    text: str
    user_id: int
```

Avoid loose dictionaries for important runtime state.

---

## DB Rules

Existing DB code should remain under:

```text
core/local/
```

Do not move DB code into UI.

UI must not call DB data sources directly.

Prefer:

```text
UI -> Service -> Repository/DataSource
```

Existing `LocalCore` and DataSource classes may be kept during refactoring.

Do not introduce a heavy repository abstraction unless it clearly simplifies the code.

---

## TTS Engine Rules

TTS engine implementations belong in:

```text
core/tts_engines/
```

Examples:

```text
gtts_engine.py
ai_stream_engine.py
stream_source.py
```

TTS engine selection policy belongs in:

```text
core/tts/engine_selector.py
```

TTS playback orchestration belongs in:

```text
core/tts/playback.py
```

---

## Development Workflow Rules

### Git History and Naming Style

Before making changes, inspect the existing Git history and code style.

Use the existing project style for:

* branch names
* commit message style
* file names
* class names
* function names
* test names
* module organization

Do not introduce a new naming convention unless explicitly requested.

Useful commands:

```bash id="wqhkpm"
git log --oneline -n 20
git branch --all
git status
```

Follow the naming patterns already used in the repository.

---

### Branch Rule

Create a new branch before starting code changes.

Do not work directly on `main` or the default branch.

Example:

```bash id="628vlf"
git checkout -b refactor/ui-core-architecture
```

If the repository already has a branch naming convention, follow that convention instead of the example.

Before creating a branch, check the current status:

```bash id="p36eg4"
git status
```

If there are existing uncommitted changes, do not overwrite or discard them without explicit user permission.

---

### Python Virtual Environment Rule

Use a Python virtual environment for all development, dependency installation, and test execution.

Prefer an existing project venv if one already exists.

If no venv exists, create one:

```bash id="wz1c8a"
python -m venv .venv
```

Activate it before running Python commands:

```bash id="6mqgzc"
source .venv/bin/activate
```

Install dependencies inside the venv only.

Do not install Python packages globally.

---

### Test Rule

Write or update tests for meaningful behavior changes.

For refactoring work, add tests around extracted Core modules when possible.

Prioritize tests for:

* pure functions
* queue behavior
* parser behavior
* formatter behavior
* service behavior
* scheduler behavior with mocks
* fallback and cleanup behavior

Do not require a real Discord connection in tests.

Use fake or mocked Discord objects instead.

Run tests before reporting completion:

```bash id="29y4zg"
pytest
```

If the full test suite cannot be run, run the most relevant subset and clearly state what was run.

---

### Commit, Push, and PR Rule

Do not commit unless the user explicitly asks.

Do not push unless the user explicitly asks.

Do not create a pull request unless the user explicitly asks.

Do not mention commit, push, or PR as the next step unless the user asks about them.

When the user explicitly asks for a commit, follow the existing commit message style from the repository history.

When the user explicitly asks to create a PR, write the PR title and body in Korean.

PR body should include:

* 작업 요약
* 주요 변경 사항
* 테스트 결과
* 주의할 점 또는 남은 작업

---

### Safety Rule for Existing Work

Never discard existing user changes without explicit permission.

Avoid commands such as:

```bash id="0d1ske"
git reset --hard
git checkout -- .
git clean -fd
```

unless the user explicitly requests them.

If uncommitted changes already exist, preserve them and work around them.

---

### Reporting Rule

When reporting progress, focus on what changed and what was verified.

Do not claim completion unless tests or relevant checks were actually run.

If some checks were not run, say so clearly.

---

## Testing Rules

When modifying architecture, keep behavior unchanged.

Use tests to protect:

* TTS queue behavior
* TTS fallback behavior
* TTS playback cleanup
* TTS source generation timeout
* Sleep timer parsing
* Sleep timer reservation creation
* Sleep timer cancellation
* Sleep timer scheduler task replacement

Do not require a real Discord connection in tests.

Use mocks or fake objects for Discord clients, guilds, members, voice clients, and interactions.

---

## Refactoring Rules

When refactoring:

1. Move code without changing behavior first.
2. Add or update tests.
3. Rename only when it improves clarity.
4. Keep commits focused by feature or layer.
5. Do not auto-commit unless explicitly requested.
6. Do not remove existing functionality.
7. Do not change user-facing messages unless needed.
8. Preserve existing custom IDs for persistent Discord Views unless explicitly changing them.

---

## Naming Rules

Use these names by default:

```text
cog.py
views.py
models.py
service.py
scheduler.py
parser.py
formatter.py
queue.py
playback.py
text_normalizer.py
engine_selector.py
```

Prefer `models.py` over `dto.py`.

Use explicit class names:

```python
TTSCog
SleepTimerCog
TTSService
SleepTimerService
SleepTimerScheduler
```

---

## Final Architecture Principle

Keep the project simple.

Use this rule when deciding where code belongs:

```text
Discord input/output code goes to ui.
Feature logic goes to core.
Shared Discord UI helpers go to ui/common.
Persistence stays in core/local.
```
