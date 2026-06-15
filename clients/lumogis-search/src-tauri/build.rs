// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

use std::fs;
use std::path::{Path, PathBuf};

fn watch_frontend_dist() {
    let dist = Path::new("../dist");
    println!("cargo:rerun-if-changed=../dist");
    let index = dist.join("index.html");
    if index.exists() {
        println!("cargo:rerun-if-changed={}", index.display());
    }
    let assets = dist.join("assets");
    if let Ok(entries) = fs::read_dir(&assets) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                println!("cargo:rerun-if-changed={}", path.display());
            }
        }
    }
}

/// Recursively merge `overlay` into `base` (overlay wins on leaves).
fn merge_json(base: &mut serde_json::Value, overlay: serde_json::Value) {
    match (base, overlay) {
        (serde_json::Value::Object(base_map), serde_json::Value::Object(overlay_map)) => {
            for (key, overlay_val) in overlay_map {
                match base_map.get_mut(&key) {
                    Some(base_val)
                        if base_val.is_object() && overlay_val.is_object() =>
                    {
                        merge_json(base_val, overlay_val);
                    }
                    _ => {
                        base_map.insert(key, overlay_val);
                    }
                }
            }
        }
        (base_slot, overlay_val) => {
            *base_slot = overlay_val;
        }
    }
}

/// Overlay patch for `TAURI_CONFIG` (merge onto base `tauri.conf.json` at compile time).
fn wdio_e2e_config_patch() -> serde_json::Value {
    let overlay_raw =
        fs::read_to_string("tauri.wdio-e2e.conf.json").expect("read tauri.wdio-e2e.conf.json");
    let mut patch: serde_json::Value =
        serde_json::from_str(&overlay_raw).expect("parse tauri.wdio-e2e.conf.json");

    let build = patch
        .as_object_mut()
        .expect("overlay root")
        .entry("build")
        .or_insert_with(|| serde_json::json!({}));
    let build_obj = build.as_object_mut().expect("overlay build");
    // Stock merge keeps base devUrl unless explicitly nulled.
    build_obj.insert("devUrl".into(), serde_json::Value::Null);
    build_obj.insert("beforeDevCommand".into(), serde_json::Value::Null);
    build_obj
        .entry("frontendDist")
        .or_insert_with(|| serde_json::json!("../dist"));

    patch
}

/// WDIO E2E: merge base + overlay into OUT_DIR for maintainer inspection.
fn write_wdio_e2e_merged_config(patch: &serde_json::Value) -> PathBuf {
    let out_dir = PathBuf::from(std::env::var("OUT_DIR").expect("OUT_DIR"));
    let merged_path = out_dir.join("tauri-e2e-merged.conf.json");

    let base_raw = fs::read_to_string("tauri.conf.json").expect("read tauri.conf.json");
    let mut merged: serde_json::Value =
        serde_json::from_str(&base_raw).expect("parse tauri.conf.json");
    merge_json(&mut merged, patch.clone());

    fs::write(
        &merged_path,
        serde_json::to_string_pretty(&merged).expect("serialize merged tauri config"),
    )
    .expect("write merged tauri config");

    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-changed=tauri.wdio-e2e.conf.json");

    merged_path
}

fn main() {
    watch_frontend_dist();

    let cap_dir = Path::new("capabilities");
    let cap_dest = cap_dir.join("wdio-e2e.json");
    let cap_template = Path::new("wdio-e2e.capability.json");

    if std::env::var("CARGO_FEATURE_WDIO_E2E").is_ok() {
        fs::create_dir_all(cap_dir).expect("capabilities dir");
        fs::copy(cap_template, &cap_dest).expect("copy wdio-e2e capability template");
        let patch = wdio_e2e_config_patch();
        let _merged_path = write_wdio_e2e_merged_config(&patch);
        // Overlay patch only — full merged JSON breaks capability resolution in generate_context!.
        let patch_json =
            serde_json::to_string(&patch).expect("serialize wdio-e2e tauri config patch");
        std::env::set_var("TAURI_CONFIG", &patch_json);
        println!("cargo:rustc-env=TAURI_CONFIG={patch_json}");
        println!(
            "cargo:warning=wdio-e2e TAURI_CONFIG patch applied ({} bytes)",
            patch_json.len()
        );
        println!("cargo:rerun-if-changed=wdio-e2e.capability.json");
    } else if cap_dest.exists() {
        fs::remove_file(&cap_dest).ok();
    }

    // Path-dep'd from lumogis-server: TAURI_CONFIG points at Server bundled conf (externalBin
    // paths under apps/lumogis-server/src-tauri/binaries). Only run tauri_build for Search.
    // Path-dep'd from lumogis-hub: hub's TAURI_CONFIG must win; skip tauri_build here.
    // Standalone `cargo build` in this crate sets CARGO_PKG_NAME=lumogis-search but leaves
    // CARGO_PRIMARY_PACKAGE empty — the old `== Ok("lumogis-search")` guard never matched,
    // so tauri_build (capabilities ACL codegen) silently never ran.
    let pkg = std::env::var("CARGO_PKG_NAME").unwrap_or_default();
    let primary = std::env::var("CARGO_PRIMARY_PACKAGE").unwrap_or_default();
    // When path-dep'd from lumogis-hub, the hub's `tauri build --config ...` exports its merged
    // TAURI_CONFIG (identifier com.lumogis.hub or com.lumogis.server, externalBin under the
    // hub's own binaries/) into the environment. Running tauri_build() here would validate the
    // HUB's resource/externalBin list against THIS crate's manifest dir and fail (e.g.
    // "binaries/orchestrator-... doesn't exist"). CARGO_PRIMARY_PACKAGE is empty for both
    // standalone and dependency builds on current cargo, so it cannot distinguish them — detect
    // the foreign leaked config directly.
    let leaked_foreign_config = std::env::var("TAURI_CONFIG")
        .map(|c| c.contains("com.lumogis.server"))
        .unwrap_or(false);
    let run_tauri_build = pkg == "lumogis-search"
        && (primary.is_empty() || primary == "lumogis-search")
        && !leaked_foreign_config;
    if run_tauri_build {
        tauri_build::build();
    }
}
