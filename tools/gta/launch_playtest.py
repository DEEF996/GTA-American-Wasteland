"""Open the verified isolated GTA copy and keep continuous J-toggle skating available."""
from __future__ import annotations
import argparse
import configparser
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from native.inspect_state import EXE_SHA256, Observer

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / 'research'
SUPERVISOR = Path(__file__).with_name('playtest.py').resolve()
ACTIVE = RESEARCH / 'active-user-playtest.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def validate_installation(game):
    game = game.resolve(strict=True)
    setup = game / 'thaw_gta/installation.json'
    if setup.exists():
        state = json.loads(setup.read_text(encoding='utf-8-sig'))
        require(state.get('format') == 'gta-thaw-installation-v1' and state.get('complete') is True
                and same_path(state['game'], game), 'Local mod installation is incomplete; finish first-run setup.')
    manifest = json.loads((game / 'thaw_gta/base-copy.json').read_text(encoding='utf-8-sig'))
    require(manifest.get('format') == 'gta-thaw-isolated-copy-v1' and
            same_path(manifest['game'], game) and not same_path(manifest['source'], game),
            'Launcher accepts only the existing isolated GTA copy.')
    executable = game / 'gta_sa.exe'
    require(hashlib.sha256(executable.read_bytes()).hexdigest() == EXE_SHA256,
            'The isolated GTA executable has changed.')
    settings = configparser.ConfigParser(inline_comment_prefixes=(';', '#'))
    settings.read(game / 'scripts/III.VC.SA.WindowedMode.ini', encoding='utf-8-sig')
    require(settings.getint('window', 'mode', fallback=0) in (1, 2),
            'Configure existing WindowedMode to windowed (1) or borderless (2).')
    require((game / 'scripts/thaw_gta.asi').is_file(), 'The GTA THAW adapter is not installed.')
    return executable


class ProcessEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                ('filename', W.WCHAR * 260)]


class Processes:
    def __init__(self):
        self.api = C.WinDLL('kernel32', use_last_error=True)
        declarations = {
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'GetExitCodeProcess': ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
            'GetProcessTimes': ([W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL),
            'QueryFullProcessImageNameW': ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
            'CreateToolhelp32Snapshot': ([W.DWORD, W.DWORD], W.HANDLE),
            'Process32FirstW': ([W.HANDLE, C.POINTER(ProcessEntry)], W.BOOL),
            'Process32NextW': ([W.HANDLE, C.POINTER(ProcessEntry)], W.BOOL),
            'CreateMutexW': ([C.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
            'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
            'ReleaseMutex': ([W.HANDLE], W.BOOL),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result

    def identity(self, pid):
        require(type(pid) is int and pid > 0, 'Invalid process identity in launcher metadata.')
        handle = self.api.OpenProcess(0x1000, False, pid)
        if not handle:
            if C.get_last_error() == 87:  # A PID which no longer exists.
                return None
            raise C.WinError(C.get_last_error())
        try:
            code = W.DWORD()
            if not self.api.GetExitCodeProcess(handle, C.byref(code)):
                raise C.WinError(C.get_last_error())
            if code.value != 259:
                return None
            path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            times = [W.FILETIME() for _ in range(4)]
            if (not self.api.QueryFullProcessImageNameW(handle, 0, path, C.byref(size)) or
                    not self.api.GetProcessTimes(handle, *(C.byref(item) for item in times))):
                raise C.WinError(C.get_last_error())
            return dict(pid=pid, creation_100ns=times[0].dwHighDateTime << 32 | times[0].dwLowDateTime,
                        executable=path.value)
        finally:
            self.api.CloseHandle(handle)

    def games(self):
        handle = self.api.CreateToolhelp32Snapshot(2, 0)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        try:
            entry = ProcessEntry(); entry.size = C.sizeof(entry)
            available = self.api.Process32FirstW(handle, C.byref(entry))
            result = []
            while available:
                if entry.filename.casefold() == 'gta_sa.exe':
                    actual = self.identity(entry.pid)
                    if actual is not None:
                        result.append(actual)
                available = self.api.Process32NextW(handle, C.byref(entry))
            if C.get_last_error() != 18:
                raise C.WinError(C.get_last_error())
            return result
        finally:
            self.api.CloseHandle(handle)


def select_game(games, executable):
    require(all(same_path(item['executable'], executable) for item in games),
            'Another GTA installation is running. Close it before starting the isolated copy.')
    require(len(games) <= 1, 'Multiple copies of the isolated GTA are already running.')
    return games[0] if games else None


def same_lifetime(actual, recorded):
    return (actual is not None and actual['pid'] == recorded.get('pid') and
            actual['creation_100ns'] == recorded.get('creation_100ns'))


def supervisor_command(pid, directory):
    return [sys.executable, '-B', str(SUPERVISOR), '--pid', str(pid),
            '--world-stream', '--continuous', '--directory', str(directory)]


def wait_for_supervisor(processes, metadata, game, *, timeout=5):
    until = time.monotonic() + timeout
    status_path = Path(metadata['logs']) / 'status.json'
    while time.monotonic() < until:
        require(same_lifetime(processes.identity(metadata['pid']), metadata),
                'The THAW supervisor ended during startup; inspect its stderr log.')
        if status_path.is_file():
            status = json.loads(status_path.read_text(encoding='utf-8-sig'))
            require(metadata['game_pid'] == game['pid'] and same_lifetime(game, status),
                    'Existing THAW supervisor belongs to another GTA process lifetime.')
            return status
        time.sleep(.1)
    raise RuntimeError('The THAW supervisor did not publish startup status within 5s.')


def existing_supervisor(processes, game, log):
    if not ACTIVE.exists():
        return None
    metadata = json.loads(ACTIVE.read_text(encoding='utf-8-sig'))
    actual = processes.identity(metadata['pid'])
    if not same_lifetime(actual, metadata):
        return None
    require(same_path(actual['executable'], sys.executable), 'Existing supervisor executable differs.')
    directory = Path(metadata['logs']).resolve()
    require(directory.is_relative_to(RESEARCH.resolve()) and directory != RESEARCH.resolve(),
            'Existing supervisor logs are outside this project.')
    wait_for_supervisor(processes, metadata, game)
    if (metadata.get('world_stream') is True and metadata.get('continuous') is True and
            same_path(metadata.get('script', ''), SUPERVISOR) and not (directory / 'STOP').exists()):
        log('reuse_supervisor', **metadata)
        return metadata
    # Only the verified older supervisor's own cooperative stop file is touched.
    (directory / 'STOP').write_text('Replacing the older playtest supervisor.\n', encoding='utf-8')
    until = time.monotonic() + 6
    while same_lifetime(processes.identity(metadata['pid']), metadata):
        require(time.monotonic() < until, 'Older THAW supervisor has not stopped; no duplicate was started.')
        time.sleep(.1)
    log('previous_supervisor_stopped', pid=metadata['pid'], logs=str(directory))
    return None


def wait_for_adapter(processes, game, executable, *, timeout=45):
    until = time.monotonic() + timeout
    error = None
    while time.monotonic() < until:
        require(same_lifetime(processes.identity(game['pid']), game), 'GTA closed during adapter startup.')
        observer = None
        try:
            observer = Observer(game['pid'])
            require(observer.created == game['creation_100ns'] and same_path(observer.exe, executable),
                    'The adapter belongs to another GTA process lifetime.')
            return  # Mapping exists; the supervisor can wait in the main menu.
        except OSError as caught:
            error = caught
        finally:
            if observer is not None:
                observer.close()
        time.sleep(.25)
    raise RuntimeError(f'THAW ASI mapping did not appear within {timeout}s: {error}')


def launch(game_path, log):
    executable = validate_installation(game_path)
    processes = Processes()
    key = hashlib.sha256(os.path.normcase(str(executable)).encode()).hexdigest()[:24]
    mutex = processes.api.CreateMutexW(None, False, 'Local\\THAW_GTA_LAUNCHER_' + key)
    if not mutex:
        raise C.WinError(C.get_last_error())
    acquired = False
    try:
        result = processes.api.WaitForSingleObject(mutex, 0)
        if result == 258:
            log('launcher_already_running')
            print('Uruchamianie GTA THAW juz trwa.')
            return
        require(result in (0, 0x80), 'Cannot acquire the GTA THAW launcher lock.')
        acquired = True
        game = select_game(processes.games(), executable)
        if game is None:
            visible = subprocess.STARTUPINFO()
            visible.dwFlags, visible.wShowWindow = subprocess.STARTF_USESHOWWINDOW, 1
            child = subprocess.Popen([str(executable)], cwd=executable.parent, startupinfo=visible)
            game = processes.identity(child.pid)
            require(game is not None and same_path(game['executable'], executable), 'The isolated GTA did not start.')
            log('game_started', **game)
        else:
            log('game_attached', **game)
        previous = existing_supervisor(processes, game, log)
        if previous is not None:
            print('GTA THAW juz dziala. W grze uzyj J.')
            return
        wait_for_adapter(processes, game, executable)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        directory = RESEARCH / ('user-playtest-' + stamp)
        with (RESEARCH / (directory.name + '.stdout.txt')).open('x') as output, \
                (RESEARCH / (directory.name + '.stderr.txt')).open('x') as errors:
            supervisor = subprocess.Popen(supervisor_command(game['pid'], directory), cwd=ROOT,
                stdout=output, stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            actual = processes.identity(supervisor.pid)
            require(actual is not None, 'The THAW supervisor ended during startup; inspect its stderr log.')
            metadata = dict(pid=supervisor.pid, creation_100ns=actual['creation_100ns'], game_pid=game['pid'],
                game_creation_100ns=game['creation_100ns'], logs=str(directory), script=str(SUPERVISOR),
                world_stream=True, continuous=True)
            temporary = ACTIVE.with_suffix('.tmp')
            temporary.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
            temporary.replace(ACTIVE)
            # Publish ownership before waiting so a retry cannot create a second supervisor.
            wait_for_supervisor(processes, metadata, game)
        except Exception:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                (directory / 'STOP').touch()
            finally:
                try:
                    supervisor.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    supervisor.kill()
                    supervisor.wait(timeout=5)
            raise
        log('supervisor_started', **metadata)
        print('GTA THAW uruchomione. Wczytaj gre i uzyj J. NumLock musi byc wlaczony.')
    finally:
        if acquired:
            processes.api.ReleaseMutex(mutex)
        processes.api.CloseHandle(mutex)


def selected_game(requested=None, *, configure=True):
    if requested is not None:
        return requested
    path = ROOT / 'installation.json'
    if path.exists():
        require(path.stat().st_size <= 4096, 'Oversized local installation selection.')
        metadata = json.loads(path.read_text(encoding='utf-8-sig'))
        require(metadata.get('format') == 'gta-thaw-installation-v1' and metadata.get('complete') is True
                and isinstance(metadata.get('game'), str) and Path(metadata['game']).is_absolute(),
                'Invalid local installation selection. Run Configure-GTA-THAW.cmd again.')
        return Path(metadata['game'])
    # Preserve the user's existing prepared playtest; this is not a source path.
    legacy = Path(r'I:\GTA_THAW')
    if (legacy / 'thaw_gta/base-copy.json').is_file():
        return legacy
    if configure:
        from install import gui
        gui()
        if path.exists():
            return selected_game(configure=False)
    raise ValueError('No completed GTA THAW installation selected. Run Configure-GTA-THAW.cmd.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, help='Prepared isolated copy; omitted uses the first-run selection')
    args = parser.parse_args()
    RESEARCH.mkdir(parents=True, exist_ok=True)
    path = RESEARCH / ('launcher-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '.jsonl')
    with path.open('x', encoding='utf-8') as output:
        def log(phase, **details):
            output.write(json.dumps(dict(phase=phase, **details), ensure_ascii=False) + '\n')
            output.flush()
        try:
            launch(selected_game(args.game), log)
        except (OSError, ValueError, RuntimeError, KeyError, configparser.Error) as error:
            log('error', message=str(error), error_type=type(error).__name__)
            print(f'Nie uruchomiono GTA THAW: {error}\nLog: {path}', file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
