from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

from minisweagent import global_config_dir

_history = FileHistory(global_config_dir / "interactive_history.txt")


class _LazyPromptSession:
    """Build the real ``PromptSession`` on first use.

    Constructing a ``PromptSession`` touches the terminal immediately and raises
    ``NoConsoleScreenBufferError`` when stdout is a pipe (a subprocess, CI, a
    sandbox). Importing the interactive agent must not depend on a terminal
    being attached, otherwise even non-interactive runs cannot be imported.
    """

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._session: PromptSession | None = None

    def prompt(self, *args, **kwargs):
        if self._session is None:
            self._session = PromptSession(**self._kwargs)
        return self._session.prompt(*args, **kwargs)


prompt_session = _LazyPromptSession(history=_history)
_multiline_prompt_session = _LazyPromptSession(history=_history, multiline=True)


def _multiline_prompt() -> str:
    return _multiline_prompt_session.prompt(
        "",
        bottom_toolbar=HTML(
            "Submit message: <b fg='yellow' bg='black'>Esc, then Enter</b> | "
            "Navigate history: <b fg='yellow' bg='black'>Arrow Up/Down</b> | "
            "Search history: <b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )
