# GTA THAW native runtime host

This Windows x86 Rust executable imports the supported user's original THAW
executable sections at their original virtual addresses. It does not start a
THAW.exe process or call the original game entry point. Original physics, script,
animation and sound code is still required and executes inside our process;
this is not a rewrite of those systems.

An explicitly ordered, zero-filled COMDAT reserves the original address range
when Windows loads our executable, before Rust or other runtime allocations.
Our actual code follows that reservation. The loader never unmaps foreign
memory, and preserves its own OS-loaded PE header and module identity.
The supported stock executable and two imported runtime DLLs are hash pinned.
The 211 original imports resolve to real system or verified original libraries,
with one narrowly scoped host adapter for `CreateEventA`. Stock FileService uses
the process-shared names `io_event` and `queue_event`; another stock instance can
consume and reset its I/O notification. The adapter recognizes only the two
pinned stock string addresses and their original arguments, and supplies names
containing this host's PID and Windows process creation time. All other event
calls pass through untouched. Original code, strings, handles and manual-reset
semantics are preserved; the readiness report includes the verified namespace.

`gta-thaw-tls.dll` contains only our eight-byte static TLS metadata, with no
entry point or callbacks. Windows supplies storage to each thread. The host
copies its assigned index into the mapped game's existing TLS-index slot.
Its compiled hash is embedded in the matching host executable.

The Python owner starts this executable suspended, assigns its kill-on-close
job, resumes only our entry point, and waits for the named ready event and JSON
report. Gameplay subsequently uses the existing serial native worker protocol.
There is no independent gameplay loop or physics approximation in this crate.

Build on Windows using `build.ps1` with an installed Rust i686 MSVC target and
Visual Studio x86 build tools. Parameters select the existing Cargo/Rustup
caches and build output. Original game files are never included in the build.
The command contract is:

```text
gta-thaw-runtime.exe --source ABS_STOCK_EXE --ready-event Local\UNIQUE_NAME --report ABS_NEW_JSON
```

The report uses `gta-thaw-rust-host-v1`; the event is signaled only after successful
mapping and import validation. The parent owns process shutdown. Mapping success
alone does not prove gameplay, animation or sound compatibility; those require
separate original native-call and live GTA regression tests.
