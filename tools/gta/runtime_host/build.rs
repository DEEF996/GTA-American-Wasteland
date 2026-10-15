fn main() {
    assert_eq!(std::env::var("TARGET").unwrap(), "i686-pc-windows-msvc");
    println!("cargo:rustc-link-arg-bin=gta-thaw-runtime=/BASE:0x400000");
    println!("cargo:rustc-link-arg-bin=gta-thaw-runtime=/DYNAMICBASE:NO");
    println!("cargo:rustc-link-arg-bin=gta-thaw-runtime=/FIXED");
    println!("cargo:rustc-link-arg-bin=gta-thaw-runtime=/DEBUG:NONE");
    println!("cargo:rustc-link-arg-bin=gta-thaw-runtime=/INCLUDE:_thaw_reservation");
    let tls_sha=std::env::var("THAW_TLS_SHA").expect("Build with build.ps1 to produce the owned TLS companion");
    assert!(tls_sha.len()==64 && tls_sha.bytes().all(|v|v.is_ascii_hexdigit()));
    println!("cargo:rustc-env=THAW_TLS_SHA={tls_sha}");
    println!("cargo:rerun-if-env-changed=THAW_TLS_SHA");
    let order = std::path::Path::new(&std::env::var("OUT_DIR").unwrap()).join("order.txt");
    std::fs::write(&order, "_thaw_reservation\n").unwrap();
    println!("cargo:rustc-link-arg-bin=gta-thaw-runtime=/ORDER:@{}", order.display());
}
