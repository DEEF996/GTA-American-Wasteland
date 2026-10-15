//! Read-only GTA adapter boundary. No THAW runtime, gameplay, pose or camera commands.
//! `--gta-observe --gta-pid PID [--seconds 10]` observes the isolated ASI's state
//! with a headless Bevy schedule. Positions use GTA world coordinates (Z up);
//! velocity and timer step remain raw GTA values, not THAW units or metres/second.
use bevy::{app::ScheduleRunnerPlugin, prelude::*};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::Read,
    path::PathBuf,
    sync::atomic::{Ordering, fence},
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, FILETIME, HANDLE, WAIT_TIMEOUT},
    System::{
        Memory::{
            FILE_MAP_READ, MEMORY_MAPPED_VIEW_ADDRESS, MapViewOfFile, OpenFileMappingW,
            UnmapViewOfFile,
        },
        SystemInformation::GetTickCount64,
        Threading::{
            GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
            QueryFullProcessImageNameW, WaitForSingleObject,
        },
    },
};

const EXE_SHA256: &str = "a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26";
const SNAPSHOT_SIZE: usize = 272;
const MAPPING_SIZE: usize = SNAPSHOT_SIZE + 8;
const PED_PRESENT: u32 = 1;

pub struct ObserveOptions {
    pid: u32,
    duration: Duration,
}

/// Accept only bounded, read-only GTA observation options.
pub fn arguments(args: impl IntoIterator<Item = String>) -> Result<Option<ObserveOptions>, String> {
    let args: Vec<_> = args.into_iter().collect();
    if !args
        .iter()
        .any(|a| matches!(a.as_str(), "--gta-observe" | "--gta-pid"))
    {
        return Ok(None);
    }
    let (mut observe, mut pid, mut seconds) = (false, None, None);
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--gta-observe" if !observe => observe = true,
            "--gta-pid" if pid.is_none() => {
                pid = Some(
                    args.next()
                        .ok_or("Missing --gta-pid")?
                        .parse::<u32>()
                        .map_err(|_| "Invalid GTA PID")?,
                );
            }
            "--seconds" if seconds.is_none() => {
                seconds = Some(
                    args.next()
                        .ok_or("Missing --seconds")?
                        .parse::<f64>()
                        .map_err(|_| "Invalid observation duration")?,
                );
            }
            _ => {
                return Err(format!(
                    "Unsupported or repeated GTA observation argument: {arg}"
                ));
            }
        }
    }
    let pid = pid
        .filter(|pid| *pid != 0)
        .ok_or("--gta-observe requires a nonzero --gta-pid")?;
    let seconds = seconds.unwrap_or(10.0);
    if !observe || !seconds.is_finite() || seconds <= 0.0 || seconds > 60.0 {
        return Err("Use --gta-observe --gta-pid PID [--seconds N], with 0 < N <= 60".into());
    }
    Ok(Some(ObserveOptions {
        pid,
        duration: Duration::from_secs_f64(seconds),
    }))
}

#[derive(Clone, Debug, Serialize)]
struct Snapshot {
    pid: u32,
    creation_100ns: u64,
    sequence: u64,
    age_ms: u64,
    capabilities: u32,
    ped_address: u32,
    epoch: u32,
    area: u32,
    ped_area: u32,
    game_timer: u32,
    frame_counter: u32,
    f7_requests: u32,
    flags: u32,
    position: [f32; 3],
    raw_gta_velocity: [f32; 3],
    heading_radians: f32,
    health: f32,
    raw_gta_time_step: f32,
    profile_path: String,
    clock_hour: u32,
    clock_minute: u32,
    clock_second: u32,
    controls_disabled_mask: u32,
    cutscene_flags: u32,
    ped_motion_flags: u32,
    grounded: bool,
    control_eligible: bool,
}

fn valid_profile(path: &str) -> bool {
    let bytes = path.as_bytes();
    bytes.len() > 3
        && bytes[0].is_ascii_alphabetic()
        && &bytes[1..3] == b":\\"
        && bytes.iter().all(|b| (32..=126).contains(b) && *b != b'/')
        && path[3..]
            .split('\\')
            .all(|part| !matches!(part, "" | "." | ".."))
}

/// Decode bytes explicitly: packed C++ floats/u64s must not become unaligned Rust references.
fn decode(
    body: &[u8],
    pid: u32,
    created: u64,
    now_ms: u64,
    expected_profile: &str,
) -> Result<Snapshot, String> {
    if body.len() != SNAPSHOT_SIZE {
        return Err("Unexpected GTA snapshot length".into());
    }
    let u32_at = |at| u32::from_le_bytes(body[at..at + 4].try_into().unwrap());
    let u64_at = |at| u64::from_le_bytes(body[at..at + 8].try_into().unwrap());
    let f32_at = |at| f32::from_bits(u32_at(at));
    if &body[..8] != b"GTATHAW2"
        || u32_at(8) != 2
        || u32_at(12) != SNAPSHOT_SIZE as u32
        || pid == 0
        || u32_at(16) != pid
        || created == 0
        || u64_at(24) != created
    {
        return Err("GTA snapshot protocol or process lifetime mismatch".into());
    }
    let (sequence, tick) = (u64_at(32), u64_at(40));
    if sequence == 0 || tick > now_ms || now_ms - tick > 1000 {
        return Err("GTA snapshot is not fresh; the game may be loading or paused".into());
    }
    if u32_at(20) & 3 != 3 || u32_at(116) != 0 {
        return Err("GTA telemetry/profile isolation prerequisites missing".into());
    }
    let profile = &body[120..248];
    let end = profile
        .iter()
        .position(|byte| *byte == 0)
        .ok_or("Unterminated GTA profile path")?;
    let profile =
        std::str::from_utf8(&profile[..end]).map_err(|_| "Invalid GTA profile encoding")?;
    if !valid_profile(profile) || !profile.eq_ignore_ascii_case(expected_profile) {
        return Err("GTA profile is not the isolated profile of the selected game".into());
    }
    let floats: Vec<_> = (80..116).step_by(4).map(f32_at).collect();
    if floats.iter().any(|value| !value.is_finite())
        || floats[..3].iter().any(|value| value.abs() > 100000.0)
        || floats[3..6].iter().any(|value| value.abs() > 10000.0)
    {
        return Err("Non-finite or out-of-bounds GTA player state".into());
    }
    let (ped, epoch, flags) = (u32_at(48), u32_at(52), u32_at(76));
    let (hour, minute, second) = (u32_at(248), u32_at(252), u32_at(256));
    let (controls, cutscene, motion) = (u32_at(260), u32_at(264), u32_at(268));
    // Original CClock's strict elapsed > period rollover can publish second 60.
    if hour > 23 || minute > 59 || second > 60 {
        return Err("Invalid GTA clock".into());
    }
    if controls > 0xffff || cutscene > 3 || motion > 7 {
        return Err("Invalid GTA scene/control state".into());
    }
    if (flags & PED_PRESENT != 0 && (ped == 0 || epoch == 0))
        || (flags & PED_PRESENT == 0 && (ped != 0 || motion != 0))
    {
        return Err("GTA player identity does not match the current scene".into());
    }
    Ok(Snapshot {
        pid,
        creation_100ns: created,
        sequence,
        age_ms: now_ms - tick,
        capabilities: u32_at(20),
        ped_address: ped,
        epoch,
        area: u32_at(56),
        ped_area: u32_at(60),
        game_timer: u32_at(64),
        frame_counter: u32_at(68),
        f7_requests: u32_at(72),
        flags,
        position: floats[..3].try_into().unwrap(),
        raw_gta_velocity: floats[3..6].try_into().unwrap(),
        heading_radians: floats[6],
        health: floats[7],
        raw_gta_time_step: floats[8],
        profile_path: profile.into(),
        clock_hour: hour,
        clock_minute: minute,
        clock_second: second,
        controls_disabled_mask: controls,
        cutscene_flags: cutscene,
        ped_motion_flags: motion,
        grounded: motion & 1 != 0,
        control_eligible: flags & 17 == 17
            && flags & (2 | 4 | 32) == 0
            && floats[7] > 0.0
            && u32_at(56) == u32_at(60)
            && controls == 0
            && cutscene == 0,
    })
}

fn complete_frame(begin: u32, end: u32, after: u32) -> bool {
    begin != 0 && begin & 1 == 0 && begin == end && begin == after
}

struct OwnedHandle(HANDLE);
impl Drop for OwnedHandle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
struct View(MEMORY_MAPPED_VIEW_ADDRESS);
impl Drop for View {
    fn drop(&mut self) {
        unsafe {
            UnmapViewOfFile(self.0);
        }
    }
}

struct Observer {
    // Drop the mapping view before its handles. None of these grant write access.
    view: View,
    _mapping: OwnedHandle,
    process: OwnedHandle,
    pid: u32,
    created: u64,
    exe: PathBuf,
    profile: String,
}

impl Observer {
    fn attach(pid: u32) -> Result<Self, String> {
        let process = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                pid,
            )
        };
        if process.is_null() {
            return Err(os_error("OpenProcess(GTA)"));
        }
        let process = OwnedHandle(process);
        let mut path = vec![0u16; 32768];
        let mut length = path.len() as u32;
        if unsafe { QueryFullProcessImageNameW(process.0, 0, path.as_mut_ptr(), &mut length) } == 0
        {
            return Err(os_error("QueryFullProcessImageNameW(GTA)"));
        }
        let exe =
            PathBuf::from(String::from_utf16(&path[..length as usize]).map_err(|e| e.to_string())?);
        if !exe
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.eq_ignore_ascii_case("gta_sa.exe"))
        {
            return Err("Selected process is not gta_sa.exe".into());
        }
        let mut file = File::open(&exe).map_err(|e| format!("Read GTA executable: {e}"))?;
        let mut hash = Sha256::new();
        let mut bytes = [0u8; 65536];
        loop {
            let count = file.read(&mut bytes).map_err(|e| e.to_string())?;
            if count == 0 {
                break;
            }
            hash.update(&bytes[..count]);
        }
        if format!("{:x}", hash.finalize()) != EXE_SHA256 {
            return Err("Unsupported GTA executable; expected the inspected GTA SA build".into());
        }
        let mut creation: FILETIME = unsafe { std::mem::zeroed() };
        let mut exit = creation;
        let mut kernel = creation;
        let mut user = creation;
        if unsafe { GetProcessTimes(process.0, &mut creation, &mut exit, &mut kernel, &mut user) }
            == 0
        {
            return Err(os_error("GetProcessTimes(GTA)"));
        }
        let created =
            (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
        let profile = exe
            .parent()
            .ok_or("GTA executable has no parent")?
            .join("thaw_gta")
            .join("profile")
            .to_str()
            .ok_or("Invalid isolated GTA profile path")?
            .to_owned();
        if !valid_profile(&profile) {
            return Err("GTA adapter requires an ASCII absolute profile path".into());
        }
        let mapping_name: Vec<u16> = format!("Local\\THAW_GTA_STATE_V2_{pid}\0")
            .encode_utf16()
            .collect();
        let mapping = unsafe { OpenFileMappingW(FILE_MAP_READ, 0, mapping_name.as_ptr()) };
        if mapping.is_null() {
            return Err(os_error(
                "OpenFileMappingW(GTA); load the isolated ASI first",
            ));
        }
        let mapping = OwnedHandle(mapping);
        let view = unsafe { MapViewOfFile(mapping.0, FILE_MAP_READ, 0, 0, MAPPING_SIZE) };
        if view.Value.is_null() {
            return Err(os_error("MapViewOfFile(GTA)"));
        }
        Ok(Self {
            view: View(view),
            _mapping: mapping,
            process,
            pid,
            created,
            exe,
            profile,
        })
    }

    fn read(&self) -> Result<Snapshot, String> {
        match unsafe { WaitForSingleObject(self.process.0, 0) } {
            WAIT_TIMEOUT => (),
            0 => return Err("GTA process ended".into()),
            _ => return Err(os_error("WaitForSingleObject(GTA)")),
        }
        let address = self.view.0.Value.cast::<u8>();
        for _ in 0..30 {
            // The external ASI publishes with InterlockedIncrement and MemoryBarrier.
            // Volatile reads and fences keep the copied bytes between both sequence reads.
            let begin = unsafe { address.cast::<u32>().read_volatile() };
            if begin == 0 || begin & 1 != 0 {
                std::hint::spin_loop();
                continue;
            }
            fence(Ordering::SeqCst);
            let mut body = [0u8; SNAPSHOT_SIZE];
            for (i, byte) in body.iter_mut().enumerate() {
                *byte = unsafe { address.add(4 + i).read_volatile() };
            }
            fence(Ordering::SeqCst);
            let end = unsafe { address.add(4 + SNAPSHOT_SIZE).cast::<u32>().read_volatile() };
            let after = unsafe { address.cast::<u32>().read_volatile() };
            if complete_frame(begin, end, after) {
                return decode(
                    &body,
                    self.pid,
                    self.created,
                    unsafe { GetTickCount64() },
                    &self.profile,
                );
            }
        }
        Err("No complete GTA state frame; start gameplay before observation".into())
    }
}

fn os_error(operation: &str) -> String {
    format!("{operation}: {}", std::io::Error::last_os_error())
}

struct Host {
    observer: Observer,
    first: Snapshot,
    last: Snapshot,
    samples: u64,
    distinct_sequences: u64,
    scene_changes: u64,
    started: Instant,
    duration: Duration,
}

/// The reusable GTA boundary owns no worker and can never activate skating.
pub fn run(options: ObserveOptions) -> Result<(), String> {
    let observer = Observer::attach(options.pid)?;
    let first = observer.read()?;
    let host = Host {
        observer,
        first: first.clone(),
        last: first,
        samples: 1,
        distinct_sequences: 1,
        scene_changes: 0,
        started: Instant::now(),
        duration: options.duration,
    };
    let exit =
        App::new()
            .add_plugins(MinimalPlugins.set(ScheduleRunnerPlugin::run_loop(
                Duration::from_secs_f64(1.0 / 60.0),
            )))
            .insert_non_send_resource(host)
            .add_systems(Update, tick)
            .run();
    if exit.is_error() {
        Err("GTA observation stopped after invalid or unavailable state".into())
    } else {
        Ok(())
    }
}

fn tick(mut host: NonSendMut<Host>, mut exit: MessageWriter<AppExit>) {
    match host.observer.read() {
        Ok(frame) if frame.sequence >= host.last.sequence => {
            host.samples += 1;
            host.distinct_sequences += u64::from(frame.sequence != host.last.sequence);
            host.scene_changes +=
                u64::from((frame.epoch, frame.area) != (host.last.epoch, host.last.area));
            host.last = frame;
        }
        result => {
            eprintln!(
                "{}",
                result
                    .err()
                    .unwrap_or_else(|| "GTA sequence moved backwards".into())
            );
            exit.write(AppExit::error());
            return;
        }
    }
    if host.started.elapsed() >= host.duration {
        println!(
            "{}",
            serde_json::json!({
                "mode": "gta-observe", "protocol": "GTATHAW2", "executable": host.observer.exe,
                "elapsed_seconds": host.started.elapsed().as_secs_f64(), "samples": host.samples,
                "distinct_sequences": host.distinct_sequences, "scene_changes": host.scene_changes,
                "coordinate_system": "GTA world, Z up", "velocity_units": "raw GTA CPhysical",
                "skating_available": false, "first": host.first, "last": host.last,
            })
        );
        exit.write(AppExit::Success);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const PROFILE: &str = r"I:\GTA_THAW\thaw_gta\profile";
    fn fixture() -> [u8; SNAPSHOT_SIZE] {
        let mut b = [0u8; SNAPSHOT_SIZE];
        b[..8].copy_from_slice(b"GTATHAW2");
        for (at, value) in [
            (8, 2u32),
            (12, SNAPSHOT_SIZE as u32),
            (16, 123),
            (20, 3),
            (48, 0x123400),
            (52, 1),
            (76, 17),
            (248, 18),
            (252, 34),
            (256, 56),
            (268, 1),
        ] {
            b[at..at + 4].copy_from_slice(&value.to_le_bytes());
        }
        for (at, value) in [(24, 456u64), (32, 1), (40, 1000)] {
            b[at..at + 8].copy_from_slice(&value.to_le_bytes());
        }
        for (i, value) in [1.0f32, 2.0, 3.0, 0.1, 0.2, 0.3, 0.5, 100.0, 1.0]
            .iter()
            .enumerate()
        {
            b[80 + 4 * i..84 + 4 * i].copy_from_slice(&value.to_le_bytes());
        }
        b[120..120 + PROFILE.len()].copy_from_slice(PROFILE.as_bytes());
        b
    }
    #[test]
    fn preserves_scene_and_raw_gta_values() {
        let s = decode(&fixture(), 123, 456, 1010, &PROFILE.to_lowercase()).unwrap();
        assert_eq!(s.position, [1.0, 2.0, 3.0]);
        assert_eq!(s.raw_gta_velocity, [0.1, 0.2, 0.3]);
        assert_eq!((s.epoch, s.age_ms, s.capabilities), (1, 10, 3));
        assert_eq!(s.heading_radians, 0.5);
        assert_eq!((s.clock_hour, s.clock_minute, s.clock_second), (18, 34, 56));
        assert!(s.grounded && s.control_eligible);
    }
    #[test]
    fn rejects_wrong_lifetime_and_stale_or_future_frames() {
        for (pid, created, now) in [
            (124, 456, 1010),
            (123, 457, 1010),
            (123, 456, 999),
            (123, 456, 2001),
        ] {
            assert!(decode(&fixture(), pid, created, now, PROFILE).is_err());
        }
        assert!(decode(&fixture()[..247], 123, 456, 1010, PROFILE).is_err());
    }
    #[test]
    fn rejects_invalid_protocol_player_and_isolation() {
        for (at, value) in [
            (8, 1u32),
            (12, 249),
            (20, 1),
            (48, 0),
            (52, 0),
            (76, 0),
            (116, 1),
            (80, f32::NAN.to_bits()),
            (80, 100001f32.to_bits()),
            (92, (-10001f32).to_bits()),
        ] {
            let mut b = fixture();
            b[at..at + 4].copy_from_slice(&value.to_le_bytes());
            assert!(decode(&b, 123, 456, 1010, PROFILE).is_err(), "offset {at}");
        }
        let mut b = fixture();
        b[120..248].fill(b'x');
        assert!(decode(&b, 123, 456, 1010, PROFILE).is_err());
        assert!(decode(&fixture(), 123, 456, 1010, r"I:\Other\thaw_gta\profile").is_err());
    }
    #[test]
    fn rejects_legacy_protocol_and_out_of_range_scene_fields() {
        for (at, value) in [
            (12, 248u32),
            (248, 24),
            (252, 60),
            (256, 61),
            (260, 65536),
            (264, 4),
            (268, 8),
        ] {
            let mut b = fixture();
            b[at..at + 4].copy_from_slice(&value.to_le_bytes());
            assert!(decode(&b, 123, 456, 1010, PROFILE).is_err(), "offset {at}");
        }
        let mut b = fixture();
        b[..8].copy_from_slice(b"GTATHAW1");
        assert!(decode(&b, 123, 456, 1010, PROFILE).is_err());
    }
    #[test]
    fn control_gate_preserves_diagnostics_and_airborne_motion() {
        for (at, value) in [
            (260, 1u32),
            (260, 128),
            (264, 1),
            (264, 2),
            (264, 3),
            (76, 17 | 2),
            (76, 17 | 32),
            (76, 5),
            (108, 0),
            (60, 1),
        ] {
            let mut b = fixture();
            b[at..at + 4].copy_from_slice(&value.to_le_bytes());
            let s = decode(&b, 123, 456, 1010, PROFILE).unwrap();
            assert!(!s.control_eligible, "offset {at}, value {value}");
        }
        let mut b = fixture();
        b[268..272].copy_from_slice(&2u32.to_le_bytes());
        let s = decode(&b, 123, 456, 1010, PROFILE).unwrap();
        assert!(!s.grounded && s.control_eligible);
        assert_eq!(s.ped_motion_flags, 2);
    }
    #[test]
    fn preserves_native_sixtieth_second_without_advancing_hour() {
        let mut b = fixture();
        for (at, value) in [(248, 23u32), (252, 59), (256, 60)] {
            b[at..at + 4].copy_from_slice(&value.to_le_bytes());
        }
        let s = decode(&b, 123, 456, 1010, PROFILE).unwrap();
        assert_eq!((s.clock_hour, s.clock_minute, s.clock_second), (23, 59, 60));
        assert!(s.control_eligible);
        b[256..260].copy_from_slice(&61u32.to_le_bytes());
        assert!(decode(&b, 123, 456, 1010, PROFILE).is_err());
    }
    #[test]
    fn validates_profile_and_seqlock() {
        for path in [
            "relative",
            r"I:relative",
            r"I:\foo\..\profile",
            r"I:\foo\.\profile",
            "I:\\foo\\",
            r"I:\\profile",
            "I:\\ł\\profile",
        ] {
            assert!(!valid_profile(path), "{path}");
        }
        assert!(complete_frame(2, 2, 2));
        for (begin, end, after) in [(0, 0, 0), (1, 1, 1), (2, 4, 2), (2, 2, 4)] {
            assert!(!complete_frame(begin, end, after));
        }
    }
    #[test]
    fn observe_cli_is_bounded_and_never_enters_legacy_modes() {
        let parse = |args: &str| arguments(args.split_whitespace().map(str::to_owned));
        assert!(parse("--help").unwrap().is_none());
        assert_eq!(
            parse("--gta-observe --gta-pid 123")
                .unwrap()
                .unwrap()
                .duration,
            Duration::from_secs(10)
        );
        for args in [
            "--gta-pid 123",
            "--gta-observe",
            "--gta-observe --gta-pid 0",
            "--gta-observe --gta-pid 1 --pid 2",
            "--gta-observe --gta-pid 1 --seconds NaN",
            "--gta-observe --gta-pid 1 --seconds 61",
            "--gta-observe --gta-pid 1 --seconds 0",
            "--gta-observe --gta-pid 1 --gta-observe",
        ] {
            assert!(parse(args).is_err(), "{args}");
        }
    }
}
