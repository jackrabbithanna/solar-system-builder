# system_library.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""UI coordination for bundled presets and the local system library."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from .models import SolarSystem
from .storage import Library


class SystemLibraryController:
    def __init__(
        self,
        parent,
        dropdown,
        save_button,
        duplicate_button,
        system_panel,
        library: Library,
        load_builtins: Callable[[], list[SolarSystem]],
        load_default: Callable[[], SolarSystem],
        current_system: Callable[[], SolarSystem],
        prepare_for_save: Callable[[SolarSystem], None],
        load_system: Callable[[SolarSystem], None],
        system_saved: Callable[[SolarSystem], None],
        system_renamed: Callable[[], None],
        activate_system: Callable[[SolarSystem], None] | None = None,
    ):
        self.parent = parent
        self.dropdown = dropdown
        self.system_panel = system_panel
        self.library = library
        self.load_builtins = load_builtins
        self.load_default = load_default
        self.current_system = current_system
        self.prepare_for_save = prepare_for_save
        self.load_system = load_system
        self.activate_system = activate_system or load_system
        self.system_saved = system_saved
        self.system_renamed = system_renamed
        self.systems: list[SolarSystem] = []
        self.updating_dropdown = False

        save_button.connect("clicked", self._on_save_clicked)
        duplicate_button.connect("clicked", self._on_duplicate_clicked)
        dropdown.connect("notify::selected", self._on_system_selected)
        system_panel.connect("system-name-edited", self._on_system_name_edited)
        system_panel.connect("delete-requested", self._on_delete_requested)

    @staticmethod
    def is_user_saved(system: SolarSystem) -> bool:
        return not system.id.startswith("builtin-")

    def refresh(self, active_system: SolarSystem | None = None) -> None:
        active_system = active_system or self.current_system()
        self.systems = [*self.load_builtins(), *self.library.list_systems()]
        active = next(
            (
                index
                for index, system in enumerate(self.systems)
                if system.id == active_system.id
            ),
            0,
        )
        self.updating_dropdown = True
        try:
            self.dropdown.set_model(
                Gtk.StringList.new(
                    [
                        active_system.name
                        if system.id == active_system.id
                        else system.name
                        for system in self.systems
                    ]
                )
            )
            self.dropdown.set_selected(active)
        finally:
            self.updating_dropdown = False

    def load_editor(self) -> None:
        system = self.current_system()
        self.system_panel.load_system(system, self.is_user_saved(system))

    def save_new_system(self, system: SolarSystem) -> None:
        self.library.save(system)
        self.activate_system(system)
        self.refresh(system)
        self.load_editor()

    def _on_system_selected(self, dropdown, _param) -> None:
        if self.updating_dropdown:
            return
        selected = dropdown.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION or selected >= len(self.systems):
            return
        chosen = self.systems[selected]
        if chosen.id != self.current_system().id:
            self.load_system(chosen)

    def _on_system_name_edited(self, _panel, name: str) -> None:
        system = self.current_system()
        if not self.is_user_saved(system) or not name:
            self.load_editor()
            return
        if name == system.name:
            return
        system.name = name
        self.system_renamed()
        self.refresh(system)

    def _on_save_clicked(self, _button) -> None:
        self.save_current()

    def save_current(self, *, refresh_dropdown: bool = True) -> SolarSystem | None:
        system = self.current_system()
        if not self.is_user_saved(system):
            return None
        self.prepare_for_save(system)
        self.library.save(system)
        self.system_saved(system)
        if refresh_dropdown:
            self.refresh(system)
        self.load_editor()
        return system

    def _on_duplicate_clicked(self, _button) -> None:
        system = self.current_system()
        self.prepare_for_save(system)
        duplicate = system.duplicate()
        self.library.save(duplicate)
        self.activate_system(duplicate)
        self.refresh(duplicate)

    def _on_delete_requested(self, *_args) -> None:
        system = self.current_system()
        if not self.is_user_saved(system):
            return

        dialog = Adw.AlertDialog.new(
            "Delete Saved System?",
            f"'{system.name}' will be permanently deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.choose(
            self.parent,
            None,
            lambda dialog, result, *_args: self._on_delete_response(
                dialog, result, system.id
            ),
        )

    def _on_delete_response(self, dialog, result, system_id: str) -> None:
        if dialog.choose_finish(result) != "delete" or system_id.startswith("builtin-"):
            return
        self.library.delete(system_id)
        default = self.load_default()
        self.activate_system(default)
        self.refresh(default)
