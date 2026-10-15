"""Bilingual desktop entry point; imports local games and launches the private copy."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import locale
from pathlib import Path
from queue import Empty, SimpleQueue
import subprocess
import sys
from threading import Event, Thread
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'tools/gta'), str(ROOT/'tools'), str(ROOT/'tools/vanilla_thaw')]
import install

FORMAT = 'gta-american-wasteland-launcher-v1'
SETTINGS = 'launcher-settings.json'
TEXT = {
    'en': {
        'tag': 'SAN ANDREAS. A WHOLE NEW LINE.',
        'subtitle': 'Original THAW skating. The streets of San Andreas.',
        'folders': 'YOUR GAMES', 'gta': 'Grand Theft Auto: San Andreas',
        'thaw': "Tony Hawk’s American Wasteland", 'browse': 'Browse…',
        'hint': 'Choose the folders containing gta_sa.exe and THAW.exe. We remember them.',
        'copy': 'MODDED COPY', 'automatic': 'Chosen automatically after selecting GTA',
        'change': 'Change…', 'copy_hint': 'A separate copy keeps your original installations untouched.',
        'ready': 'Choose your two games, then press Play.',
        'returning': 'Ready to play. Your folders have been saved.',
        'play': 'PLAY', 'working': 'PREPARING…', 'cancel': 'Cancel',
        'checking': 'Checking your local installation…', 'launching': 'Starting GTA and the skating engine…',
        'started': 'Game started. Load a save or start a new game, then press J.',
        'cancelling': 'Stopping safely after the current operation…',
        'cancelled': 'Setup stopped. Partial files are kept; choose a new modded-copy folder to retry.',
        'failed': 'Could not start. Details are shown below; your original games are unchanged.',
        'controls': 'J  ·  Skate mode     F8  ·  Settings     NumLock  ·  On',
        'foot': 'Local imports only · No game files included · No separate runtime installation',
        'first': 'First Play copies GTA and imports THAW. Internet is needed for three verified plugins.',
        'errors': 'Details', 'missing': 'Select both game folders first.',
        'output_title': 'Choose the parent folder for a NEW modded copy',
        'language': 'Language', 'docs': 'Instructions',
        'folder_error': 'The selected folder does not contain {name}.',
        'output_error': 'Choose a short modded-copy path using English letters, for example D:\\Games\\GTA_AW.',
    },
    'pl': {
        'tag': 'SAN ANDREAS. ZUPEŁNIE NOWA LINIA.',
        'subtitle': 'Oryginalna jazda z THAW. Ulice San Andreas.',
        'folders': 'TWOJE GRY', 'gta': 'Grand Theft Auto: San Andreas',
        'thaw': "Tony Hawk’s American Wasteland", 'browse': 'Wybierz…',
        'hint': 'Wybierz foldery z gta_sa.exe i THAW.exe. Zapamiętamy je.',
        'copy': 'KOPIA Z MODEM', 'automatic': 'Wybierana automatycznie po wskazaniu GTA',
        'change': 'Zmień…', 'copy_hint': 'Osobna kopia pozostawia oryginalne instalacje bez zmian.',
        'ready': 'Wybierz obie gry i naciśnij Graj.',
        'returning': 'Gotowe do gry. Foldery zostały zapamiętane.',
        'play': 'GRAJ', 'working': 'PRZYGOTOWANIE…', 'cancel': 'Anuluj',
        'checking': 'Sprawdzanie lokalnej instalacji…', 'launching': 'Uruchamianie GTA i silnika jazdy…',
        'started': 'Gra uruchomiona. Wczytaj zapis lub rozpocznij grę, potem naciśnij J.',
        'cancelling': 'Bezpieczne zatrzymanie po bieżącej operacji…',
        'cancelled': 'Przerwano. Zachowano częściowe pliki; wybierz nowy folder kopii, aby ponowić.',
        'failed': 'Nie udało się uruchomić. Szczegóły poniżej; oryginalne gry pozostały bez zmian.',
        'controls': 'J  ·  Tryb deski     F8  ·  Ustawienia     NumLock  ·  Włączony',
        'foot': 'Import lokalny · Bez plików gier w paczce · Bez instalowania środowiska',
        'first': 'Pierwsze Graj kopiuje GTA i importuje THAW. Trzy sprawdzone dodatki wymagają internetu.',
        'errors': 'Szczegóły', 'missing': 'Najpierw wybierz foldery obu gier.',
        'output_title': 'Wybierz folder nadrzędny dla NOWEJ kopii z modem',
        'language': 'Język', 'docs': 'Instrukcja',
        'folder_error': 'Wybrany folder nie zawiera {name}.',
        'output_error': 'Wybierz krótką ścieżkę kopii bez polskich znaków, np. D:\\Games\\GTA_AW.',
    },
}

PHASES = {
    'Checking original game versions...': 'Sprawdzanie wersji oryginalnych gier…',
    'Copying GTA to the isolated folder...': 'Kopiowanie GTA do osobnego folderu…',
    'Preparing original THAW runtime': 'Przygotowanie oryginalnego silnika THAW…',
    'Importing original THAW scripts': 'Importowanie skryptów THAW…',
    'Importing original profile and statistics': 'Importowanie profilu i statystyk…',
    'Importing skeleton, animations and board': 'Importowanie szkieletu, animacji i deski…',
    'Importing original HUD and sound resources': 'Importowanie interfejsu i dźwięków…',
    'Importing original wood, metal and fence sounds': 'Importowanie dźwięków powierzchni…',
    'Building private HUD atlases': 'Przygotowanie lokalnego interfejsu…',
    'Installing adapter, 120 FPS fixes and borderless mode...': 'Instalowanie moda, poprawek 120 FPS i trybu bez ramek…',
}


def read_json(path, limit=16384):
    path = install.plain_path(path)
    if path.stat().st_size > limit:
        raise ValueError('Local launcher metadata is oversized: '+path.name)
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('Local launcher metadata must be an object: '+path.name)
    return result


def default_language():
    return 'pl' if (locale.getlocale()[0] or '').lower().startswith('pl') else 'en'


def load_settings(root=ROOT):
    result = dict(format=FORMAT, language=default_language(), gta='', thaw='', game='')
    path = root/SETTINGS
    if path.exists():
        loaded = read_json(path)
        if loaded.get('format') != FORMAT or loaded.get('language') not in TEXT:
            raise ValueError('Unsupported launcher settings. Move launcher-settings.json aside to reset.')
        for key in ('gta', 'thaw', 'game'):
            value = loaded.get(key)
            if not isinstance(value, str) or len(value) > 32767 or '\0' in value:
                raise ValueError('Invalid saved launcher folder: '+key)
        result.update({key: loaded[key] for key in result})
    elif (root/'installation.json').exists():
        selection = read_json(root/'installation.json')
        if selection.get('format') == install.FORMAT and selection.get('complete') is True:
            game = Path(selection['game'])
            if not game.is_absolute():
                raise ValueError('Local installation path must be absolute.')
            state = read_json(game/'thaw_gta/installation.json')
            if state.get('complete') is True and state.get('format') == install.FORMAT:
                result.update(gta=state['source'], thaw=state['thaw_source'], game=str(game))
    return result


def save_settings(settings, root=ROOT):
    install.write_json(root/SETTINGS, dict(settings, format=FORMAT))


def source_folders(settings):
    language = settings['language']
    if not settings['gta'].strip() or not settings['thaw'].strip():
        raise ValueError(TEXT[language]['missing'])
    gta = install.setup_sources.locate(settings['gta'], 'gta_sa.exe', 'GTA San Andreas')
    thaw = install.setup_sources.locate(settings['thaw'], 'THAW.exe', 'Game')
    return gta, thaw


def suggested_destination(gta):
    return Path(gta).absolute().parent/'GTA_American_Wasteland'


def launch_hidden(game, log, *, root=ROOT):
    interpreter = Path(sys.executable)
    if interpreter.name.casefold() == 'pythonw.exe':
        interpreter = interpreter.with_name('python.exe')
    interpreter = install.plain_path(interpreter).resolve(strict=True)
    # Use the existing CLI in a console interpreter with no console window.
    # Its supervisor and multiprocessing workers keep their real process identity.
    command = [str(interpreter), '-E', '-s', '-B', str(root/'tools/gta/launch_playtest.py'), '--game', str(game)]
    result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8', errors='replace')
    log('game_launcher_finished', exit_code=result.returncode, output=result.stdout[-16384:])
    if result.returncode:
        raise RuntimeError(result.stdout.strip()[-16384:] or 'GTA could not start. Check the research folder for launcher logs.')


def prepare_and_play(settings, *, root=ROOT, progress=lambda *a, **k: None,
                     stopped=lambda: False, install_game=None, launch_game=None):
    """All slow work runs off the UI thread; reuse only a completed matching import."""
    if install_game is None:
        install_game = install.install
    if launch_game is None:
        launch_game = lambda game, log: launch_hidden(game, log, root=root)
    gta, thaw = source_folders(settings)
    destination = install.plain_path(settings['game'] or suggested_destination(gta), missing=True).resolve()
    local = dict(settings, gta=str(gta), thaw=str(thaw), game=str(destination))
    save_settings(local, root)
    install.check_cancel(stopped)
    progress(TEXT[settings['language']]['checking'])
    if destination.exists():
        state = read_json(destination/'thaw_gta/installation.json')
        if (state.get('format') != install.FORMAT or state.get('complete') is not True or
                Path(state.get('game', '')).resolve() != destination or
                Path(state.get('source', '')).resolve() != gta or
                Path(state.get('thaw_source', '')).resolve() != thaw):
            raise ValueError('This output is incomplete or belongs to other source games. Choose a NEW modded-copy folder.')
        _, build = install.adapter_build(root)
        installed_adapter = install.plain_path(destination/'scripts/thaw_gta.asi')
        if (state.get('adapter_sha256') != build['asi_sha256'] or
                install.setup_sources.digest(installed_adapter) != build['asi_sha256']):
            raise ValueError('This copy uses a different mod version or a changed adapter. Choose a NEW modded-copy folder to install this release.')
    else:
        install_game(gta, thaw, destination, root=root, progress=progress, stopped=stopped)
    install.check_cancel(stopped)
    # Keep the existing command-line launcher in sync with the GUI selection.
    install.write_json(root/'installation.json', dict(format=install.FORMAT, complete=True, game=str(destination)))
    logs = root/'research'
    logs.mkdir(exist_ok=True)
    path = logs/('desktop-launcher-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')+'.jsonl')
    progress(TEXT[settings['language']]['launching'])
    with path.open('x', encoding='utf-8') as output:
        def log(phase, **details):
            output.write(json.dumps(dict(phase=phase, **details), ensure_ascii=False)+'\n')
            output.flush()
        launch_game(destination, log)
    return local


class Launcher:
    BG, PANEL, FIELD = '#11151c', '#1b222c', '#111820'
    FG, MUTED, LIME, LINE = '#f3f6f8', '#a7b3c2', '#d2fa69', '#344250'

    def __init__(self, window, *, root=ROOT):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.window, self.root = tk, ttk, window, root
        self.events, self.cancelled = SimpleQueue(), Event()
        self.worker = None
        self.working = False
        self.phase = ''
        self.status_key = 'ready'
        self.close_after_cancel = False
        self.settings_error = None
        try:
            self.settings = load_settings(root)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.settings = dict(format=FORMAT, language=default_language(), gta='', thaw='', game='')
            self.settings_error = str(error)
        self.language = self.settings['language']
        self.values = {key: tk.StringVar(value=self.settings[key]) for key in ('gta', 'thaw', 'game')}
        self.widgets, self.controls = {}, []
        window.title('GTA American Wasteland')
        window.configure(bg=self.BG)
        window.geometry('900x720')
        window.minsize(850, 700)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        self.icon = tk.PhotoImage(width=32, height=32)
        self.icon.put(self.BG, to=(0, 0, 32, 32))
        self.icon.put(self.LIME, to=(5, 10, 27, 20))
        self.icon.put(self.FG, to=(8, 22, 12, 26))
        self.icon.put(self.FG, to=(20, 22, 24, 26))
        window.iconphoto(True, self.icon)
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('AW.TEntry', fieldbackground=self.FIELD, foreground=self.FG,
                        bordercolor=self.LINE, insertcolor=self.FG, padding=8)
        style.map('AW.TEntry', fieldbackground=[('disabled', self.PANEL)])
        style.configure('AW.Horizontal.TProgressbar', troughcolor=self.FIELD,
                        background=self.LIME, bordercolor=self.FIELD, lightcolor=self.LIME, darkcolor=self.LIME)
        style.configure('AW.TCombobox', fieldbackground=self.FIELD, background=self.PANEL,
                        foreground=self.FG, arrowcolor=self.LIME, bordercolor=self.LINE, padding=5)
        style.map('AW.TCombobox', fieldbackground=[('readonly', self.FIELD)],
                  foreground=[('readonly', self.FG)], selectbackground=[('readonly', self.FIELD)])
        window.option_add('*TCombobox*Listbox.background', self.PANEL)
        window.option_add('*TCombobox*Listbox.foreground', self.FG)
        header = tk.Frame(window, bg=self.BG)
        header.grid(row=0, column=0, sticky='ew', padx=32, pady=(20, 14))
        header.columnconfigure(0, weight=1)
        self.label(header, 'tag', fg=self.LIME, font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, sticky='w')
        languages = ttk.Combobox(header, values=('English', 'Polski'), state='readonly',
                                width=9, style='AW.TCombobox', takefocus=True)
        languages.current(1 if self.language == 'pl' else 0)
        self.language_picker = languages
        languages.grid(row=0, column=1, sticky='e')
        languages.bind('<<ComboboxSelected>>', lambda event: self.set_language('pl' if languages.current() else 'en'))
        self.controls.append(languages)
        tk.Label(header, text='GTA', bg=self.BG, fg=self.FG, font=('Segoe UI', 30, 'bold'),
                 anchor='w').grid(row=1, column=0, sticky='w', pady=(4, 0))
        tk.Label(header, text='AMERICAN WASTELAND', bg=self.BG, fg=self.FG, font=('Segoe UI', 18, 'bold'),
                 anchor='w').grid(row=2, column=0, columnspan=2, sticky='w')
        self.label(header, 'subtitle', fg=self.MUTED).grid(row=3, column=0, columnspan=2, sticky='w', pady=(4, 0))
        card = tk.Frame(window, bg=self.PANEL, highlightbackground=self.LINE, highlightthickness=1)
        card.grid(row=1, column=0, sticky='nsew', padx=32)
        card.columnconfigure(0, weight=1)
        self.label(card, 'folders', bg=self.PANEL, fg=self.LIME,
                   font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', padx=22, pady=(14, 8))
        for index, key in enumerate(('gta', 'thaw')):
            row = 1+index*2
            self.label(card, key, bg=self.PANEL, font=('Segoe UI', 11, 'bold')).grid(row=row, column=0, sticky='w', padx=22)
            entry = ttk.Entry(card, textvariable=self.values[key], style='AW.TEntry', font=('Segoe UI', 10))
            entry.grid(row=row+1, column=0, sticky='ew', padx=(22, 10), pady=(5, 8))
            self.controls.append(entry)
            entry.bind('<FocusOut>', lambda event, key=key: self.choose_default(key))
            button = self.button(card, 'browse', lambda key=key: self.browse(key))
            button.grid(row=row+1, column=1, padx=(0, 22), pady=(5, 8))
            self.controls.append(button)
        self.label(card, 'hint', bg=self.PANEL, fg=self.MUTED, font=('Segoe UI', 9)).grid(
            row=5, column=0, columnspan=2, sticky='w', padx=22, pady=(0, 8))
        tk.Frame(card, bg=self.LINE, height=1).grid(row=6, column=0, columnspan=2, sticky='ew', padx=22)
        output = tk.Frame(card, bg=self.PANEL)
        output.grid(row=7, column=0, columnspan=2, sticky='ew', padx=22, pady=(8, 0))
        output.columnconfigure(1, weight=1)
        self.label(output, 'copy', bg=self.PANEL, fg=self.MUTED, font=('Segoe UI', 9, 'bold')).grid(row=0, column=0, sticky='w')
        self.output_label = tk.Label(output, bg=self.PANEL, fg=self.FG, anchor='w', font=('Segoe UI', 9))
        self.output_label.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        change = self.button(output, 'change', lambda: self.browse('game'))
        change.grid(row=0, column=2, rowspan=2, padx=(12, 0))
        self.controls.append(change)
        self.label(card, 'copy_hint', bg=self.PANEL, fg=self.MUTED, font=('Segoe UI', 9)).grid(
            row=8, column=0, columnspan=2, sticky='w', padx=22, pady=(4, 10))
        footer = tk.Frame(window, bg=self.BG)
        footer.grid(row=2, column=0, sticky='ew', padx=32, pady=(10, 12))
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar()
        tk.Label(footer, textvariable=self.status, bg=self.BG, fg=self.FG, anchor='w', justify='left',
                 wraplength=800, font=('Segoe UI', 10)).grid(row=0, column=0, columnspan=3, sticky='ew')
        self.progress = ttk.Progressbar(footer, style='AW.Horizontal.TProgressbar', mode='determinate', maximum=100)
        self.progress.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(8, 8))
        self.label(footer, 'controls', fg=self.MUTED, font=('Segoe UI', 10)).grid(row=2, column=0, sticky='w')
        self.cancel_button = self.button(footer, 'cancel', self.cancel)
        self.cancel_button.grid(row=2, column=1, padx=10)
        self.cancel_button.configure(state='disabled')
        self.play_button = self.button(footer, 'play', self.start, primary=True)
        self.play_button.grid(row=2, column=2)
        self.label(footer, 'first', fg=self.MUTED, font=('Segoe UI', 9)).grid(row=3, column=0, columnspan=3, sticky='w', pady=(8, 3))
        self.label(footer, 'foot', fg=self.MUTED, font=('Segoe UI', 9)).grid(row=4, column=0, columnspan=3, sticky='w')
        self.details = tk.Text(footer, height=4, bg=self.FIELD, fg='#ffb6a6', relief='flat', wrap='word',
                               font=('Consolas', 9), state='disabled')
        self.details.grid(row=5, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        self.details.grid_remove()
        for value in self.values.values():
            value.trace_add('write', lambda *args: self.update_output())
        window.bind('<Return>', lambda event: self.start() if not self.busy else None)
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.status_key = 'returning' if all(self.values[key].get() for key in self.values) else 'ready'
        self.translate()
        if self.settings_error:
            self.show_error(self.settings_error)
        window.after(100, self.tick)

    @property
    def busy(self):
        return self.working

    def label(self, parent, key, **options):
        options = {'bg': self.BG, 'fg': self.FG, 'anchor': 'w', 'font': ('Segoe UI', 11), **options}
        label = self.tk.Label(parent, **options)
        self.widgets.setdefault(key, []).append(label)
        return label

    def button(self, parent, key, command, primary=False):
        button = self.tk.Button(parent, command=command, relief='flat', bd=0,
            bg=self.LIME if primary else self.LINE, fg=self.BG if primary else self.FG,
            activebackground='#e3ff9c' if primary else '#46586c', activeforeground=self.BG if primary else self.FG,
            disabledforeground='#81909d', font=('Segoe UI', 12 if primary else 10, 'bold'),
            padx=28 if primary else 15, pady=8, cursor='hand2', takefocus=True,
            highlightthickness=1, highlightbackground=self.LINE, highlightcolor=self.LIME)
        self.widgets.setdefault(key, []).append(button)
        return button

    def translate(self):
        self.language_picker.current(1 if self.language == 'pl' else 0)
        for key, widgets in self.widgets.items():
            for widget in widgets:
                widget.configure(text=TEXT[self.language][key])
        self.status.set(TEXT[self.language][self.status_key])
        self.update_output()

    def set_language(self, language):
        if self.busy:
            return
        self.language = language
        self.translate()
        try:
            save_settings(self.snapshot(), self.root)
        except (OSError, ValueError) as error:
            self.show_error(str(error))

    def snapshot(self):
        return dict(format=FORMAT, language=self.language, **{key: value.get().strip() for key, value in self.values.items()})

    def update_output(self):
        text = self.values['game'].get() or TEXT[self.language]['automatic']
        self.output_label.configure(text=text if len(text) <= 82 else '…'+text[-81:])

    def choose_default(self, key):
        if key == 'gta' and self.values['gta'].get().strip() and not self.values['game'].get():
            self.values['game'].set(str(suggested_destination(self.values['gta'].get().strip())))

    def browse(self, key):
        from tkinter import filedialog
        value = self.values[key].get()
        selected = filedialog.askdirectory(parent=self.window,
            title=TEXT[self.language]['output_title' if key == 'game' else key],
            initialdir=value if value and Path(value).is_dir() else None, mustexist=True)
        if selected:
            self.values[key].set(str(Path(selected)/'GTA_American_Wasteland') if key == 'game' else selected)
            self.choose_default(key)

    def show_error(self, message):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', message)
        self.details.configure(state='disabled')
        self.details.grid()
        self.window.geometry('900x800')

    def start(self):
        if self.busy:
            return
        self.choose_default('gta')
        values = self.snapshot()
        if not values['gta'] or not values['thaw']:
            self.show_error(TEXT[self.language]['missing'])
            return
        self.cancelled.clear()
        self.working = True
        self.details.grid_remove()
        self.status_key = 'checking'
        self.status.set(TEXT[self.language][self.status_key])
        self.play_button.configure(state='disabled', text=TEXT[self.language]['working'])
        self.cancel_button.configure(state='normal')
        for control in self.controls:
            control.configure(state='disabled')
        self.progress.configure(mode='indeterminate')
        self.progress.start(15)
        def run():
            try:
                result = prepare_and_play(values, root=self.root, stopped=self.cancelled.is_set,
                    progress=lambda text, **details: self.events.put(('progress', text, details)))
                self.events.put(('done', result, {}))
            except install.Cancelled as error:
                self.events.put(('cancelled', str(error), {}))
            except Exception as error:
                self.events.put(('error', str(error), {}))
        self.worker = Thread(target=run, name='GTA-American-Wasteland-setup')
        self.worker.start()

    def cancel(self):
        self.cancelled.set()
        self.status_key = 'cancelling'
        self.status.set(TEXT[self.language][self.status_key])
        self.cancel_button.configure(state='disabled')

    def close(self):
        if self.busy:
            self.close_after_cancel = True
            self.cancel()
        else:
            self.window.destroy()

    def tick(self):
        try:
            while True:
                kind, value, details = self.events.get_nowait()
                if kind == 'progress':
                    if self.cancelled.is_set():
                        continue
                    if self.language == 'pl':
                        value = next((translated for english, translated in PHASES.items() if value.startswith(english)), value)
                        if value.startswith('Downloading '):
                            value = 'Pobieranie '+value.removeprefix('Downloading ')
                    if not value.startswith('Ready. Start Run-'):
                        self.status.set(value)
                    if details.get('total'):
                        self.progress.stop()
                        self.progress.configure(mode='determinate', maximum=details['total'], value=details['done'])
                    elif self.progress['mode'] != 'indeterminate':
                        self.progress.configure(mode='indeterminate')
                        self.progress.start(15)
                else:
                    self.working = False
                    self.progress.stop()
                    self.progress.configure(mode='determinate', maximum=100, value=100 if kind == 'done' else 0)
                    self.play_button.configure(state='normal', text=TEXT[self.language]['play'])
                    self.cancel_button.configure(state='disabled')
                    for control in self.controls:
                        control.configure(state='readonly' if isinstance(control, self.ttk.Combobox) else 'normal')
                    self.status_key = {'done': 'started', 'cancelled': 'cancelled', 'error': 'failed'}[kind]
                    self.status.set(TEXT[self.language][self.status_key])
                    if kind == 'done':
                        for key in self.values:
                            self.values[key].set(value[key])
                    elif kind == 'error':
                        self.show_error(value)
                    if self.close_after_cancel:
                        self.window.destroy()
                        return
        except Empty:
            pass
        self.window.after(100, self.tick)


def main():
    import tkinter as tk
    window = tk.Tk()
    Launcher(window)
    window.mainloop()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        report = traceback.format_exc()
        try:
            (ROOT/'launcher-error.log').write_text(report, encoding='utf-8')
        except OSError:
            pass
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            'The launcher could not open. Extract the complete package to a writable folder.\n'
            'Nie można otworzyć launchera. Rozpakuj całą paczkę do zapisywalnego folderu.\n\n'+report,
            'GTA American Wasteland', 0x10)
        raise SystemExit(1)
