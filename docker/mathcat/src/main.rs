//! MathML in, speech and braille out, one formula per line (JSON lines on stdin and stdout).
//!
//! Each input line: {"mathml": "<math>...</math>"}. Each output line, in the same order:
//! {"speech": "...", "nemeth": "⠭⠘⠆", "ueb": "⠭⠰⠔⠼⠃"} or {"error": "..."} for MathML MathCAT can't take.
//! Speech is ClearSpeak in English; braille is Unicode braille cells. One process reads a whole document's formulas.
use libmathcat::interface::*;
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};

fn read(mathml: &str) -> Value {
    if let Err(e) = set_mathml(mathml.to_string()) {
        return json!({ "error": errors_to_string(&e) });
    }
    let mut out = json!({});
    match get_spoken_text() {
        Ok(s) => out["speech"] = json!(s),
        Err(e) => out["error"] = json!(errors_to_string(&e)),
    }
    for (code, key) in [("Nemeth", "nemeth"), ("UEB", "ueb")] {
        let braille = set_preference("BrailleCode".to_string(), code.to_string())
            .and_then(|_| get_braille(String::new()));
        match braille {
            Ok(b) => out[key] = json!(b),
            Err(e) => out[format!("{key}_error")] = json!(errors_to_string(&e)),
        }
    }
    out
}

fn main() {
    if std::env::args().any(|a| a == "--version") {
        println!("mathcat {}", get_version());
        return;
    }
    let setup = set_rules_dir("Rules".to_string())
        .and_then(|_| set_preference("Language".to_string(), "en".to_string()))
        .and_then(|_| set_preference("SpeechStyle".to_string(), "ClearSpeak".to_string()))
        .and_then(|_| set_preference("Verbosity".to_string(), "Medium".to_string()));
    if let Err(e) = setup {
        eprintln!("mathcat-cli: {}", errors_to_string(&e));
        std::process::exit(2);
    }
    let stdout = io::stdout();
    let mut out = stdout.lock();
    for line in io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let request: Value = serde_json::from_str(&line).unwrap_or(Value::Null);
        let answer = match request["mathml"].as_str() {
            Some(m) => read(m),
            None => json!({ "error": "no mathml" }),
        };
        if writeln!(out, "{answer}").and_then(|_| out.flush()).is_err() {
            break;
        }
    }
}
