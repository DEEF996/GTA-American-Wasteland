//! Isolate the stock file service's two named events inside this owned host.
//! All original code/data and event semantics remain unchanged.
use crate::image::{check, sha256, Result, BASE};
use std::{ffi::c_void, ptr, sync::OnceLock};

pub const IMPORT_SLOT: usize = 0x68e190;
const ORIGINAL_NAMES: [(usize, &[u8]); 2] = [
    (0x6a2554, b"io_event\0"), (0x6a2548, b"queue_event\0"),
];
type CreateEvent = unsafe extern "system" fn(*mut c_void, i32, i32, *const u8) -> *mut c_void;
struct Binding {
    real: CreateEvent,
    namespace: String,
    created: u64,
    names: [Vec<u8>; 2],
}
static BINDING: OnceLock<Binding> = OnceLock::new();

#[repr(C)]
#[derive(Default)]
struct FileTime { low: u32, high: u32 }
#[link(name="kernel32")]
unsafe extern "system" {
    fn GetCurrentProcess() -> *mut c_void;
    fn GetCurrentProcessId() -> u32;
    fn GetProcessTimes(process:*mut c_void, created:*mut FileTime, exited:*mut FileTime,
                       kernel:*mut FileTime, user:*mut FileTime) -> i32;
    fn SetLastError(error:u32);
}

fn names(pid:u32, created:u64) -> Result<(String, [Vec<u8>; 2])> {
    check(pid!=0 && created!=0, "File event namespace requires a real process lifetime")?;
    let namespace=format!("Local\\GTA_THAW_FILE_{pid}_{created:016x}");
    let events=[format!("{namespace}_io\0").into_bytes(),
                format!("{namespace}_queue\0").into_bytes()];
    Ok((namespace,events))
}

fn validate_names(memory:&[u8]) -> Result<()> {
    for (address, expected) in ORIGINAL_NAMES {
        let offset=address-BASE;
        check(memory.get(offset..offset+expected.len())==Some(expected),
              "Original file event name differs")?;
    }
    Ok(())
}

#[derive(Debug,PartialEq)]
enum Route { PassThrough, File(usize) }
fn route(name:usize, security:usize, manual:i32, initial:i32) -> Route {
    match ORIGINAL_NAMES.iter().position(|(address,_)|*address==name) {
        None=>Route::PassThrough,
        Some(index) if security==0 && manual==1 && initial==0=>Route::File(index),
        Some(_)=>Route::PassThrough,
    }
}

unsafe extern "system" fn create_event(security:*mut c_void, manual:i32, initial:i32,
                                       name:*const u8) -> *mut c_void {
    let Some(binding)=BINDING.get() else {
        unsafe { SetLastError(5023) }; // ERROR_INVALID_STATE; never create a shared fallback.
        return ptr::null_mut();
    };
    unsafe { dispatch(binding,security,manual,initial,name) }
}

unsafe fn dispatch(binding:&Binding,security:*mut c_void,manual:i32,initial:i32,
                   name:*const u8) -> *mut c_void {
    let selected=match route(name as usize, security as usize, manual, initial) {
        Route::PassThrough=>name, // Do not dereference unrelated callers' pointers.
        Route::File(index)=>{
            let expected=ORIGINAL_NAMES[index].1;
            // Only the exact, pinned readonly stock string addresses reach here.
            if unsafe { std::slice::from_raw_parts(name,expected.len()) }!=expected {
                unsafe { SetLastError(13) }; // ERROR_INVALID_DATA.
                return ptr::null_mut();
            }
            binding.names[index].as_ptr()
        },
    };
    // Preserve the actual HANDLE, failure and GetLastError from Windows.
    unsafe { (binding.real)(security,manual,initial,selected) }
}

pub fn install(memory:&[u8], real:usize) -> Result<usize> {
    check(real!=0 && BINDING.get().is_none(), "File event import is absent or already bound")?;
    validate_names(memory)?;
    for (address,size,digest) in [
        (0x62a2e0,298,"13c52a994b2c3bb932feaf5875ad4b9419ab2d81e7a0985babc8ef824f87d9ff"),
        (0x62a040,367,"2007721cc9de57e85db99717430136cc5e87cd8639a6c244bc071b59d16fd674"),
    ] {
        let code=memory.get(address-BASE..address-BASE+size).ok_or("File event code is truncated")?;
        check(sha256(code)?==digest,"Original file event startup/worker differs")?;
    }
    let (mut created,mut exited,mut kernel,mut user)=
        (FileTime::default(),FileTime::default(),FileTime::default(),FileTime::default());
    check(unsafe {GetProcessTimes(GetCurrentProcess(),&mut created,&mut exited,&mut kernel,&mut user)}!=0,
          "Cannot identify own process lifetime for file events")?;
    let created=(u64::from(created.high)<<32)|u64::from(created.low);
    let (namespace,names)=names(unsafe {GetCurrentProcessId()},created)?;
    let real=unsafe { std::mem::transmute::<usize,CreateEvent>(real) };
    BINDING.set(Binding{real,namespace,created,names}).map_err(|_|"File event import initialized twice")?;
    Ok(create_event as *const () as usize)
}

pub fn identity() -> Result<(&'static str,u64)> {
    BINDING.get().map(|value|(value.namespace.as_str(),value.created))
        .ok_or("File event import isolation was not initialized".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn only_exact_stock_pointers_and_arguments_are_rewritten() {
        for (index,(address,_)) in ORIGINAL_NAMES.iter().enumerate() {
            assert_eq!(route(*address,0,1,0),Route::File(index));
            for (security,manual,initial) in [(1,1,0),(0,0,0),(0,1,1),(0,2,0)] {
                assert_eq!(route(*address,security,manual,initial),Route::PassThrough);
            }
        }
        for pointer in [0,1,0x6a2555,0x6a2549,b"io_event\0".as_ptr() as usize] {
            assert_eq!(route(pointer,17,0,1),Route::PassThrough);
        }
    }
    #[test] fn original_names_are_bounded_and_pinned() {
        let mut memory=vec![0u8;0x2a2560];
        for (address,name) in ORIGINAL_NAMES {
            memory[address-BASE..address-BASE+name.len()].copy_from_slice(name);
        }
        assert!(validate_names(&memory).is_ok());
        assert!(validate_names(&memory[..0x2a2558]).is_err());
        memory[0x2a2554]=b'x';assert!(validate_names(&memory).is_err());
    }
    #[test] fn namespace_separates_pid_and_reused_pid_lifetime() {
        let (prefix,a)=names(123,0x123456789abcdef).unwrap();
        assert_eq!(prefix,"Local\\GTA_THAW_FILE_123_0123456789abcdef");
        assert_ne!(a[0],a[1]);
        assert_ne!(a,names(124,0x123456789abcdef).unwrap().1);
        assert_ne!(a,names(123,0x123456789abcdf0).unwrap().1);
        assert!(a.iter().all(|v|v.last()==Some(&0) && v.len()<240));
        assert!(names(0,1).is_err() && names(1,0).is_err());
    }

    #[test] fn stdcall_passthrough_preserves_arguments_handle_and_last_error() {
        unsafe extern "system" { fn GetLastError()->u32; }
        unsafe extern "system" fn real(security:*mut c_void,manual:i32,initial:i32,name:*const u8)->*mut c_void {
            assert!(matches!((security as usize,manual,initial,name as usize),
                             (0x1234,3,4,1)|(0,0,0,0x6a2554)));
            unsafe {SetLastError(4242)};
            0x5678usize as *mut c_void
        }
        let (namespace,names)=names(123,789).unwrap();
        let binding=Binding{real,namespace,created:789,names};
        let result=unsafe {dispatch(&binding,0x1234usize as *mut c_void,3,4,1usize as *const u8)};
        let error=unsafe {GetLastError()};
        assert_eq!(result as usize,0x5678);assert_eq!(error,4242);
        // A different call using the same name pointer also passes through;
        // only the pinned initializer's exact argument tuple is intercepted.
        let result=unsafe {dispatch(&binding,ptr::null_mut(),0,0,0x6a2554usize as *const u8)};
        let error=unsafe {GetLastError()};
        assert_eq!(result as usize,0x5678);assert_eq!(error,4242);
    }
}
