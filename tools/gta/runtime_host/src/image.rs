//! Pinned stock sections only; the operating system owns our executable header.
use std::{ffi::c_void, fs, path::Path, ptr};
pub type Result<T> = std::result::Result<T, String>;
pub const BASE: usize = 0x400000;
pub const IMAGE_BYTES: usize = 0x184d000;
pub const FILE_BYTES: usize = 21_442_560;
pub const SHA: &str = "ff3351529586eefba74d85a807282e18101844e68ecaa5cee20a0a16bed0b118";
#[link(name="kernel32")]
unsafe extern "system" {
    fn VirtualProtect(address:*mut c_void, bytes:usize, flags:u32, old:*mut u32)->i32;
    fn GetCurrentProcess()->*mut c_void;
    fn FlushInstructionCache(process:*mut c_void, address:*const c_void, bytes:usize)->i32;
    fn LoadLibraryExW(name:*const u16, file:*mut c_void, flags:u32)->*mut c_void;
    fn GetProcAddress(module:*mut c_void, name:*const u8)->*mut c_void;
    fn SetDefaultDllDirectories(flags:u32)->i32;
    fn GetLastError()->u32;
}
#[link(name="bcrypt")]
unsafe extern "system" {
    fn BCryptOpenAlgorithmProvider(handle:*mut *mut c_void, name:*const u16, provider:*const u16, flags:u32)->i32;
    fn BCryptCloseAlgorithmProvider(handle:*mut c_void, flags:u32)->i32;
    fn BCryptHash(handle:*mut c_void, secret:*const u8, secret_len:u32, input:*const u8, input_len:u32, output:*mut u8, output_len:u32)->i32;
}
pub fn check(ok: bool, message: &str) -> Result<()> {
    if ok { Ok(()) } else { Err(message.into()) }
}
fn span(data: &[u8], at: usize, count: usize) -> Result<&[u8]> {
    data.get(at..at.checked_add(count).ok_or("Range overflow")?)
        .ok_or("Truncated input".into())
}
fn u16_at(data: &[u8], at: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(span(data, at, 2)?.try_into().unwrap()))
}
fn u32_at(data: &[u8], at: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(span(data, at, 4)?.try_into().unwrap()))
}
pub fn sha256(data: &[u8]) -> Result<String> {
    let name: Vec<u16> = "SHA256\0".encode_utf16().collect();
    let mut handle = ptr::null_mut();
    check(
        unsafe { BCryptOpenAlgorithmProvider(&mut handle, name.as_ptr(), ptr::null(), 0) } >= 0,
        "SHA provider unavailable",
    )?;
    let mut hash = [0; 32];
    let status = unsafe {
        BCryptHash(
            handle,
            ptr::null(),
            0,
            data.as_ptr(),
            data.len().try_into().map_err(|_| "Hash input too large")?,
            hash.as_mut_ptr(),
            32,
        )
    };
    unsafe {
        BCryptCloseAlgorithmProvider(handle, 0);
    }
    check(status >= 0, "SHA calculation failed")?;
    Ok(hash.iter().map(|b| format!("{b:02x}")).collect())
}
pub fn json(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
#[derive(Clone, Copy, Debug, PartialEq)]
struct Section {
    rva: usize,
    len: usize,
    raw: usize,
    raw_len: usize,
    flags: u32,
}
const SECTIONS: [Section; 9] = [
    Section {
        rva: 0x1000,
        len: 0x28d000,
        raw: 0x400,
        raw_len: 0x28c384,
        flags: 0x60000020,
    },
    Section {
        rva: 0x28e000,
        len: 0x2e000,
        raw: 0x28d000,
        raw_len: 0x2db07,
        flags: 0x40000040,
    },
    Section {
        rva: 0x2bc000,
        len: 0x195000,
        raw: 0x2bb000,
        raw_len: 0x1e37c,
        flags: 0xc0000040,
    },
    Section {
        rva: 0x451000,
        len: 0x1000,
        raw: 0x2da000,
        raw_len: 1,
        flags: 0xc0000040,
    },
    Section {
        rva: 0x452000,
        len: 0x5000,
        raw: 0x2db000,
        raw_len: 0x4fb4,
        flags: 0x40000040,
    },
    Section {
        rva: 0x457000,
        len: 0x770000,
        raw: 0x2e0000,
        raw_len: 0x76fe90,
        flags: 0xe0000020,
    },
    Section {
        rva: 0xbc7000,
        len: 0x10000,
        raw: 0xa50000,
        raw_len: 0xf5b0,
        flags: 0xe0000020,
    },
    Section {
        rva: 0xbd7000,
        len: 0x3d9000,
        raw: 0xa60000,
        raw_len: 0x3d8a85,
        flags: 0xe0000040,
    },
    Section {
        rva: 0xfb0000,
        len: 0x89d000,
        raw: 0xe39000,
        raw_len: 0x639a38,
        flags: 0xe0000020,
    },
];
fn validate_spans(sections: &[Section], file_len: usize) -> Result<()> {
    let mut end = 0x1000;
    for s in sections {
        check(
            s.rva >= end
                && s.rva % 4096 == 0
                && s.len > 0
                && s.len % 4096 == 0
                && s.raw_len <= s.len,
            "Invalid/overlapping mapped section",
        )?;
        end = s.rva.checked_add(s.len).ok_or("Section overflow")?;
        check(
            end <= IMAGE_BYTES && s.raw.checked_add(s.raw_len).is_some_and(|v| v <= file_len),
            "Section exceeds source or mapping",
        )?;
    }
    Ok(())
}
fn parse(data: &[u8]) -> Result<usize> {
    check(
        data.len() == FILE_BYTES && sha256(data)? == SHA,
        "Unsupported source EXE hash/size",
    )?;
    check(span(data, 0, 2)? == b"MZ", "Missing DOS header")?;
    let pe = u32_at(data, 0x3c)? as usize;
    check(
        span(data, pe, 4)? == b"PE\0\0"
            && u16_at(data, pe + 4)? == 0x14c
            && u16_at(data, pe + 6)? == 9,
        "Unsupported PE header",
    )?;
    let opt = pe + 24;
    check(
        u16_at(data, opt)? == 0x10b
            && u32_at(data, opt + 28)? as usize == BASE
            && u32_at(data, opt + 56)? as usize == IMAGE_BYTES
            && u32_at(data, opt + 60)? == 0x1000,
        "Unsupported PE32 extent",
    )?;
    check(
        u32_at(data, opt + 136)? == 0 && u32_at(data, opt + 140)? == 0,
        "Unexpected relocation table",
    )?;
    let table = opt + u16_at(data, pe + 20)? as usize;
    for (i, expected) in SECTIONS.iter().enumerate() {
        let at = table + i * 40;
        let actual = Section {
            rva: u32_at(data, at + 12)? as usize,
            len: u32_at(data, at + 8)? as usize,
            raw: u32_at(data, at + 20)? as usize,
            raw_len: u32_at(data, at + 16)? as usize,
            flags: u32_at(data, at + 36)?,
        };
        check(actual == *expected, "Pinned section layout differs")?;
    }
    validate_spans(&SECTIONS, data.len())?;
    Ok(u32_at(data, opt + 104)? as usize)
}
fn c_string(data: &[u8], at: usize) -> Result<&str> {
    let available = data
        .len()
        .checked_sub(at)
        .ok_or("String outside image")?
        .min(260);
    let tail = span(data, at, available)?;
    let n = tail
        .iter()
        .position(|&b| b == 0)
        .ok_or("Unterminated import string")?;
    let value = std::str::from_utf8(&tail[..n]).map_err(|_| "Non-ASCII import")?;
    check(!value.is_empty() && value.is_ascii(), "Invalid import name")?;
    Ok(value)
}

fn protect(rva:usize,len:usize,flags:u32)->Result<()> {
    let mut old=0;
    check(unsafe { VirtualProtect((BASE+rva) as *mut c_void,len,flags,&mut old) }!=0,
        "Cannot change protection inside the owned stock reservation")
}

pub fn tls()->Result<u32> {
    let path=std::env::current_exe().map_err(|e|e.to_string())?.with_file_name("gta-thaw-tls.dll");
    let raw=fs::read(&path).map_err(|e|e.to_string())?;
    check(sha256(&raw)?==env!("THAW_TLS_SHA"),"Owned TLS companion hash differs from this host build")?;
    use std::os::windows::ffi::OsStrExt;
    let wide:Vec<u16>=path.as_os_str().encode_wide().chain([0]).collect();
    let module=unsafe {LoadLibraryExW(wide.as_ptr(),ptr::null_mut(),0x1100)};
    check(!module.is_null(),"Cannot load owned static TLS metadata")?;
    let index=unsafe {GetProcAddress(module,c"thaw_tls_index".as_ptr().cast())} as *const u32;
    check(!index.is_null(),"Owned TLS index export is missing")?;
    Ok(unsafe {index.read()})
}

fn bind_imports(memory:&mut [u8],mut descriptor:usize,source:&Path)->Result<()> {
    let expected=[("ws2_32.dll",28),("d3d9.dll",1),("d3dx9_27.dll",17),
        ("binkw32.dll",13),("winmm.dll",3),("dsound.dll",2),("rpcrt4.dll",1),
        ("kernel32.dll",111),("user32.dll",23),("advapi32.dll",9),("ole32.dll",1),
        ("wininet.dll",1),("dinput8.dll",1)];
    check(unsafe { SetDefaultDllDirectories(0x800) }!=0,"Cannot restrict system DLL resolution")?;
    let mut total=0;
    for (dll,count) in expected {
        let lookup=u32_at(memory,descriptor)? as usize;
        let iat=u32_at(memory,descriptor+16)? as usize;
        check(c_string(memory,u32_at(memory,descriptor+12)? as usize)?.to_ascii_lowercase()==dll && lookup!=0,
            "Original import descriptor differs")?;
        let dependency=match dll {
            "binkw32.dll"=>Some((377856,"e3d19e66b6135925116c8e7585b6e6e3746e50d79f1ec863cda615b0189df1a9")),
            "d3dx9_27.dll"=>Some((2319568,"a70d571cd675c97c9eeb4a234dba1d667ffb54ec3bb14defb36b3e2f605ae257")),
            _=>None,
        };
        let (name,flags)=if let Some((bytes,hash))=dependency {
            let path=source.parent().ok_or("Source directory absent")?.join(dll);
            let raw=fs::read(&path).map_err(|e|e.to_string())?;
            check(raw.len()==bytes && sha256(&raw)?==hash,"Original runtime DLL hash differs")?;
            (path.as_os_str().to_os_string(),0x1100)
        } else {(std::ffi::OsString::from(dll),0x800)};
        use std::os::windows::ffi::OsStrExt;
        let wide:Vec<u16>=name.encode_wide().chain([0]).collect();
        let module=unsafe { LoadLibraryExW(wide.as_ptr(),ptr::null_mut(),flags) };
        check(!module.is_null(),&format!("Cannot load verified {dll}: {}",unsafe{GetLastError()}))?;
        for n in 0..count {
            let value=u32_at(memory,lookup+n*4)?;
            check(value!=0 && iat+n*4>=0x28e000 && iat+n*4+4<=0x28e380,"Original IAT bounds differ")?;
            let pointer=if value&0x80000000!=0 { (value&0xffff) as usize as *const u8 }
                else {c_string(memory,value as usize+2)?; unsafe {memory.as_ptr().add(value as usize+2)}};
            let mut address=unsafe { GetProcAddress(module,pointer) } as usize;
            check(address!=0,"Verified runtime DLL is missing an original import")?;
            if BASE+iat+n*4==crate::file_events::IMPORT_SLOT {
                check(dll=="kernel32.dll" && value&0x80000000==0 &&
                      c_string(memory,value as usize+2)?=="CreateEventA", "File event import slot differs")?;
                address=crate::file_events::install(memory,address)?;
            }
            memory[iat+n*4..iat+n*4+4].copy_from_slice(&(address as u32).to_le_bytes());
            total+=1;
        }
        check(u32_at(memory,lookup+count*4)?==0,"Unexpected extra original import")?;
        descriptor+=20;
    }
    check(total==211 && span(memory,descriptor,20)?.iter().all(|&v|v==0),"Import directory extent differs")
}

pub fn map(source:&Path,tls_index:u32)->Result<String> {
    unsafe extern "C" { static thaw_reservation:u8; }
    check((&raw const thaw_reservation) as usize==BASE+0x1000 &&
        map as *const () as usize>=BASE+IMAGE_BYTES,"Host linker reservation layout differs")?;
    let data=fs::read(source).map_err(|e|e.to_string())?;
    let imports=parse(&data)?;
    let memory=unsafe { std::slice::from_raw_parts_mut(BASE as *mut u8,IMAGE_BYTES) };
    check(memory[0x1000..].iter().all(|&v|v==0),"Owned stock reservation is not pristine")?;
    protect(0x1000,IMAGE_BYTES-0x1000,4)?;
    for section in SECTIONS {
        memory[section.rva..section.rva+section.raw_len].copy_from_slice(&data[section.raw..section.raw+section.raw_len]);
    }
    // Do not replace our OS-loaded PE headers. The original code/data sections
    // occupy their exact VAs; host TLS/import/load-config ownership remains real.
    check([0x851000,0x851008,0x84d8f8,0x6bc250,0,0].iter().enumerate().all(|(i,&v)|
        u32_at(memory,0x2a76b0+i*4).ok()==Some(v)) &&
        u32_at(memory,0x2bc250)?==0 && memory[0x451000..0x451008]==[0;8],
        "Stock static TLS extent/callbacks changed")?;
    check(tls_index<1088,"OS TLS index exceeds supported bound")?;
    memory[0x44d8f8..0x44d8fc].copy_from_slice(&tls_index.to_le_bytes());
    bind_imports(memory,imports,source)?;
    let text_hash=sha256(&memory[SECTIONS[0].rva..SECTIONS[0].rva+SECTIONS[0].raw_len])?;
    check(text_hash==sha256(&data[SECTIONS[0].raw..SECTIONS[0].raw+SECTIONS[0].raw_len])?,"Original text was changed by the host")?;
    for section in SECTIONS {
        let flags=match (section.flags&0x20000000!=0,section.flags&0x80000000!=0) {
            (true,true)=>0x40,(true,false)=>0x20,(false,true)=>4,(false,false)=>2,
        };
        protect(section.rva,section.len,flags)?;
    }
    check(unsafe{FlushInstructionCache(GetCurrentProcess(),BASE as *const c_void,IMAGE_BYTES)}!=0,"Cannot flush imported instruction cache")?;
    Ok(text_hash)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn exact_section_boundaries() { validate_spans(&SECTIONS,FILE_BYTES).unwrap(); }
    #[test] fn overlapping_or_truncated_sections_rejected() {
        let mut bad=SECTIONS;bad[1].rva=bad[0].rva;
        assert!(validate_spans(&bad,FILE_BYTES).is_err());
        let last=SECTIONS.last().unwrap();assert!(validate_spans(&SECTIONS,last.raw+last.raw_len-1).is_err());
    }
    #[test] fn imports_are_bounded_ascii() {
        assert_eq!(c_string(b"kernel32.dll\0",0).unwrap(),"kernel32.dll");
        assert!(c_string(&[b'x';261],0).is_err());assert!(c_string(b"x\0",3).is_err());
    }
    #[test] fn windows_hash_and_json_escape() {
        assert_eq!(sha256(b"abc").unwrap(),"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(json("a\"\\\n"),"\"a\\\"\\\\\\u000a\"");
    }
}

