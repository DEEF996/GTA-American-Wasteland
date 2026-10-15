//! Our x86 process hosts unchanged stock gameplay code; never launches THAW.exe.
mod image;
mod file_events;
use image::{check, json, Result};
use std::{ffi::c_void, fs, io::Write, path::PathBuf, ptr};

std::arch::global_asm!(r#"
    .section .text,"xr",discard,_thaw_reservation
    .globl _thaw_reservation
_thaw_reservation:
    .space 0x184c000, 0
"#);

#[link(name="kernel32")]
unsafe extern "system" {
    fn GetModuleHandleW(name:*const u16)->*mut c_void;
    fn GetCurrentProcessId()->u32;
    fn OpenEventW(access:u32, inherit:i32, name:*const u16)->*mut c_void;
    fn SetEvent(event:*mut c_void)->i32;
    fn CloseHandle(handle:*mut c_void)->i32;
    fn Sleep(milliseconds:u32);
    fn SetErrorMode(mode:u32)->u32;
    fn ExitProcess(code:u32)->!;
}

struct Arguments { source: PathBuf, report: PathBuf, ready_event: String }
fn arguments(values: impl Iterator<Item=std::ffi::OsString>) -> Result<Arguments> {
    let mut values=values;
    let (mut source,mut report,mut ready_event)=(None,None,None);
    while let Some(key)=values.next() {
        let value=values.next().ok_or("Missing argument value")?;
        match key.to_str() {
            Some("--source") if source.is_none()=>source=Some(PathBuf::from(value)),
            Some("--report") if report.is_none()=>report=Some(PathBuf::from(value)),
            Some("--ready-event") if ready_event.is_none()=>ready_event=Some(value.into_string().map_err(|_|"Invalid event name")?),
            _=>return Err("Unknown or repeated argument".into()),
        }
    }
    let args=Arguments { source:source.ok_or("Missing --source")?, report:report.ok_or("Missing --report")?,ready_event:ready_event.ok_or("Missing --ready-event")? };
    check(args.source.is_absolute() && args.report.is_absolute(),"Source and report paths must be absolute")?;
    check(args.ready_event.starts_with("Local\\") && args.ready_event.len()<=240 &&
        args.ready_event[6..].bytes().all(|v|v.is_ascii_alphanumeric() || matches!(v,b'_'|b'-')),
        "Invalid local ready-event name")?;
    Ok(args)
}

fn run() -> Result<()> {
    let args=arguments(std::env::args_os().skip(1))?;
    let mut report=fs::OpenOptions::new().write(true).create_new(true).open(&args.report).map_err(|e|e.to_string())?;
    let outcome=(||->Result<String>{
        let host_base=unsafe { GetModuleHandleW(ptr::null()) } as usize;
        check(host_base==image::BASE,"Own host must occupy the reserved stock base")?;
        let tls_index=image::tls()?;
        let native = image::map(&args.source,tls_index)?;
        let (file_namespace,created)=file_events::identity()?;
        let event:Vec<u16>=args.ready_event.encode_utf16().chain([0]).collect();
        let event=unsafe { OpenEventW(2,0,event.as_ptr()) };
        check(!event.is_null(),"Parent ready event is unavailable")?;
        let record=format!("{{\"format\":\"gta-thaw-rust-host-v1\",\"pid\":{},\"host_executable\":{},\"source_executable\":{},\"source_sha256\":\"{}\",\"image_base\":{},\"image_size\":{},\"sections_mapped\":9,\"mapped_original_headers\":false,\"original_entrypoint_called\":false,\"original_process_created\":false,\"tls_callbacks_called\":false,\"static_tls_bytes\":8,\"static_tls_index\":{},\"imports_bound\":211,\"stock_text_sha256\":{},\"file_events_isolated\":true,\"file_event_namespace\":{},\"created_100ns\":{},\"ready\":true}}\n",
            unsafe{GetCurrentProcessId()},json(&std::env::current_exe().map_err(|e|e.to_string())?.to_string_lossy()),
            json(&args.source.to_string_lossy()),image::SHA,image::BASE,image::IMAGE_BYTES,tls_index,json(&native),
            json(file_namespace),created);
        report.write_all(record.as_bytes()).and_then(|_|report.flush()).map_err(|e|e.to_string())?;
        report.sync_all().map_err(|e|e.to_string())?;
        let signaled=unsafe { SetEvent(event) };
        unsafe { CloseHandle(event) };
        check(signaled!=0,"Could not signal parent readiness")?;
        // Parent's noninherited kill-on-close job is the lifetime owner. Never
        // unload the image while an original resource worker could still run.
        unsafe { Sleep(u32::MAX) };
        Err("Lifetime wait unexpectedly returned".into())
    })();
    match outcome {
        Ok(_)=>Ok(()),
        Err(error)=>{
            let _=report.set_len(0);
            use std::io::{Seek,SeekFrom};
            let _=report.seek(SeekFrom::Start(0));
            let _=writeln!(report,"{{\"format\":\"gta-thaw-rust-host-v1\",\"ready\":false,\"error\":{}}}",json(&error));
            let _=report.flush();
            Err(error)
        }
    }
}
fn main() {
    unsafe { SetErrorMode(0x8003) };
    if let Err(error)=run() { eprintln!("GTA THAW runtime: {error}"); unsafe { ExitProcess(1) } }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn args(v:&[&str])->Result<Arguments> { arguments(v.iter().map(std::ffi::OsString::from)) }
    #[test] fn launch_arguments_are_strict() {
        assert!(args(&["--source","C:\\game\\THAW.exe","--report","C:\\new.json","--ready-event","Local\\THAW_GTA_test"]).is_ok());
        assert!(args(&["--source","relative.exe","--report","C:\\new.json","--ready-event","Local\\test"]).is_err());
        assert!(args(&["--source","C:\\game.exe","--report","C:\\new.json","--ready-event","Global\\test"]).is_err());
        assert!(args(&["--source","C:\\game.exe","--source","C:\\again.exe"]).is_err());
    }
}
