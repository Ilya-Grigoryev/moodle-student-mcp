"""Process-wide application context (lazy, so the server starts even when not configured yet)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import html, timeutil
from .client import MoodleClient
from .config import Settings, load_settings
from .courses import CourseIndex

if TYPE_CHECKING:
    from .state import State
    from .web import WebSession


class App:
    def __init__(self) -> None:
        self._settings: Settings | None = None
        self._client: MoodleClient | None = None
        self._courses: CourseIndex | None = None
        self._state: State | None = None
        self._web: WebSession | None = None

    def configure(self, settings: Settings) -> None:
        self._settings = settings
        self._client = MoodleClient(settings)
        self._courses = CourseIndex(self._client)
        self._state = None
        self._web = None
        timeutil.set_timezone(settings.timezone)
        html.set_language(settings.lang)

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self.configure(load_settings())
        assert self._settings is not None
        return self._settings

    @property
    def client(self) -> MoodleClient:
        _ = self.settings
        assert self._client is not None
        return self._client

    @property
    def courses(self) -> CourseIndex:
        _ = self.settings
        assert self._courses is not None
        return self._courses

    @property
    def state(self) -> State:
        if self._state is None:
            from .state import State

            self._state = State(self.settings.state_dir, self.settings.site_url)
        return self._state

    @property
    def web(self) -> WebSession:
        if self._web is None:
            from .web import WebSession

            self._web = WebSession(self.client, self.state)
        return self._web

    def site(self, path: str) -> str:
        return self.settings.site_url + path

    def mod_url(self, modname: str, cmid: int) -> str:
        return self.site(f"/mod/{modname}/view.php?id={cmid}")


app = App()
