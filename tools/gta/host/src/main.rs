#[cfg(windows)]
mod gta_bridge;

#[cfg(not(windows))]
compile_error!("The GTA adapter host requires Windows.");

#[cfg(windows)]
fn main() {
    const USAGE: &str = "Usage: gta-thaw-host --gta-observe --gta-pid PID [--seconds N (0 < N <= 60)]\nRead-only GTA telemetry; no skating runtime.";
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() == 1 && args[0] == "--help" {
        println!("{USAGE}");
        return;
    }
    let result = gta_bridge::arguments(args)
        .and_then(|options| options.ok_or_else(|| USAGE.to_owned()))
        .and_then(gta_bridge::run);
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
