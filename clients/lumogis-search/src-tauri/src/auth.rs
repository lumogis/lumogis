// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

//! Overlay session keychain + orchestrator auth (LUM-397 C5).

use crate::MemorySearchResponseDto;
use reqwest::header::HeaderMap;
use reqwest::StatusCode;
use serde::{Deserialize, Serialize};
use sha2::Digest;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio_util::sync::CancellationToken;

const KEYCHAIN_SERVICE: &str = "lumogis-overlay";
const SESSION_ACCOUNT_PREFIX: &str = "session:";
const LEGACY_TOKEN_ACCOUNT_PREFIX: &str = "access_token:";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum AuthMode {
    Unknown,
    Off,
    On,
    Unreachable,
}

impl AuthMode {
    fn from_probe_status(status: StatusCode) -> Self {
        if status == StatusCode::OK {
            AuthMode::Off
        } else if status == StatusCode::UNAUTHORIZED {
            AuthMode::On
        } else {
            AuthMode::Unreachable
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthSession {
    pub access_token: String,
    pub refresh_token: String,
    pub expires_at_unix: i64,
    pub role: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AuthSessionPublic {
    pub role: String,
    pub expires_at_unix: i64,
    pub present: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AuthProbeResult {
    pub mode: String,
    pub session_present: bool,
    pub role: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
struct LoginRequestBody {
    email: String,
    password: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "snake_case")]
struct UserPublicDto {
    id: String,
    email: String,
    role: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "snake_case")]
struct LoginResponseDto {
    access_token: String,
    expires_in: i64,
    user: UserPublicDto,
}

pub fn normalise_base_url(mut s: String) -> String {
    while s.ends_with('/') {
        s.pop();
    }
    s
}

pub fn host_for_keyring(base: &str) -> String {
    url::Url::parse(base)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_string()))
        .unwrap_or_else(|| "unknown-host".to_string())
}

fn account_short(host: &str) -> String {
    let digest = format!("{:x}", sha2::Sha256::digest(host.as_bytes()));
    digest[..digest.len().min(8)].to_string()
}

fn session_entry(host: &str) -> keyring::Result<keyring::Entry> {
    keyring::Entry::new(
        KEYCHAIN_SERVICE,
        &format!("{}{}", SESSION_ACCOUNT_PREFIX, account_short(host)),
    )
}

fn legacy_token_entry(host: &str) -> keyring::Result<keyring::Entry> {
    keyring::Entry::new(
        KEYCHAIN_SERVICE,
        &format!("{}{}", LEGACY_TOKEN_ACCOUNT_PREFIX, account_short(host)),
    )
}

fn migrate_legacy_token(host: &str) {
    let Ok(legacy) = legacy_token_entry(host) else {
        return;
    };
    if legacy.get_password().is_ok() {
        let _ = legacy.delete_credential();
    }
}

pub fn read_session(host: &str) -> Result<Option<AuthSession>, String> {
    let entry = session_entry(host).map_err(|e| e.to_string())?;
    match entry.get_password() {
        Ok(raw) if !raw.is_empty() => {
            serde_json::from_str(&raw).map_err(|e| e.to_string()).map(Some)
        }
        Ok(_) => {
            migrate_legacy_token(host);
            Ok(None)
        }
        Err(keyring::Error::NoEntry) => {
            migrate_legacy_token(host);
            Ok(None)
        }
        Err(e) => Err(e.to_string()),
    }
}

pub fn write_session(host: &str, session: &AuthSession) -> Result<(), String> {
    let json = serde_json::to_string(session).map_err(|e| e.to_string())?;
    session_entry(host)
        .map_err(|e| e.to_string())?
        .set_password(&json)
        .map_err(|e| e.to_string())
}

pub fn clear_session(host: &str) -> Result<(), String> {
    let e = session_entry(host).map_err(|e| e.to_string())?;
    let _ = e.delete_credential();
    let _ = legacy_token_entry(host).and_then(|l| l.delete_credential());
    Ok(())
}

pub fn session_public(host: &str) -> Result<AuthSessionPublic, String> {
    match read_session(host)? {
        Some(s) => Ok(AuthSessionPublic {
            role: s.role,
            expires_at_unix: s.expires_at_unix,
            present: true,
        }),
        None => Ok(AuthSessionPublic {
            role: String::new(),
            expires_at_unix: 0,
            present: false,
        }),
    }
}

fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

pub fn parse_lumogis_refresh_from_headers(headers: &HeaderMap) -> Option<String> {
    for value in headers.get_all("set-cookie") {
        let Ok(s) = value.to_str() else {
            continue;
        };
        for part in s.split(',') {
            if let Some(v) = parse_cookie_pair(part.trim(), "lumogis_refresh") {
                return Some(v);
            }
        }
        if let Some(v) = parse_cookie_pair(s, "lumogis_refresh") {
            return Some(v);
        }
    }
    None
}

fn parse_cookie_pair(set_cookie: &str, name: &str) -> Option<String> {
    let first = set_cookie.split(';').next()?.trim();
    let (k, v) = first.split_once('=')?;
    if k.trim() == name {
        Some(v.to_string())
    } else {
        None
    }
}

pub fn http_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())
}

fn upload_http_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())
}

fn map_reqwest_error(e: reqwest::Error) -> String {
    if e.is_timeout() {
        "timeout".into()
    } else if e.is_connect() {
        "connection_failed".into()
    } else {
        e.to_string()
    }
}

pub async fn probe_auth(base: &str) -> Result<AuthProbeResult, String> {
    let url = format!("{}/api/v1/auth/me", normalise_base_url(base.to_string()));
    let client = http_client()?;
    let resp = client.get(&url).send().await.map_err(map_reqwest_error)?;
    let status = resp.status();
    let mode = AuthMode::from_probe_status(status);
    let host = host_for_keyring(base);
    let session = read_session(&host)?;
    let role = session.as_ref().map(|s| s.role.clone());
    Ok(AuthProbeResult {
        mode: auth_mode_label(mode).to_string(),
        session_present: session.is_some(),
        role,
    })
}

fn auth_mode_label(mode: AuthMode) -> &'static str {
    match mode {
        AuthMode::Off => "off",
        AuthMode::On => "on",
        AuthMode::Unreachable => "unreachable",
        AuthMode::Unknown => "unknown",
    }
}

pub async fn login(base: &str, email: String, password: String) -> Result<AuthSession, String> {
    if password.len() < 12 {
        return Err("password_too_short".into());
    }
    let url = format!("{}/api/v1/auth/login", normalise_base_url(base.to_string()));
    let client = http_client()?;
    let body = LoginRequestBody { email, password };
    let resp = client
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(map_reqwest_error)?;
    let status = resp.status();
    if status == StatusCode::SERVICE_UNAVAILABLE {
        return Err("auth_disabled".into());
    }
    if status == StatusCode::UNAUTHORIZED {
        return Err("invalid_credentials".into());
    }
    if status == StatusCode::TOO_MANY_REQUESTS {
        return Err("rate_limited".into());
    }
    if !status.is_success() {
        return Err(format!("http_{}", status.as_u16()));
    }
    let headers = resp.headers().clone();
    let parsed: LoginResponseDto = resp.json().await.map_err(|e| e.to_string())?;
    let refresh = parse_lumogis_refresh_from_headers(&headers)
        .ok_or_else(|| "missing_refresh_cookie".to_string())?;
    let session = AuthSession {
        access_token: parsed.access_token,
        refresh_token: refresh,
        expires_at_unix: now_unix() + parsed.expires_in,
        role: parsed.user.role,
    };
    let host = host_for_keyring(base);
    write_session(&host, &session)?;
    Ok(session)
}

async fn refresh_session(base: &str, session: &AuthSession) -> Result<AuthSession, String> {
    if session.refresh_token.is_empty() {
        return Err("session_expired".into());
    }
    let url = format!("{}/api/v1/auth/refresh", normalise_base_url(base.to_string()));
    let client = http_client()?;
    let resp = client
        .post(&url)
        .header(
            "Authorization",
            format!("Bearer {}", session.access_token),
        )
        .header("Cookie", format!("lumogis_refresh={}", session.refresh_token))
        .send()
        .await
        .map_err(map_reqwest_error)?;
    let status = resp.status();
    if status == StatusCode::FORBIDDEN {
        return Err("auth_csrf_misconfig".into());
    }
    if status == StatusCode::SERVICE_UNAVAILABLE {
        return Err("auth_disabled".into());
    }
    if status == StatusCode::UNAUTHORIZED {
        return Err("session_expired".into());
    }
    if !status.is_success() {
        return Err(format!("http_{}", status.as_u16()));
    }
    let headers = resp.headers().clone();
    let parsed: LoginResponseDto = resp.json().await.map_err(|e| e.to_string())?;
    let refresh = parse_lumogis_refresh_from_headers(&headers).unwrap_or_else(|| {
        session.refresh_token.clone()
    });
    let updated = AuthSession {
        access_token: parsed.access_token,
        refresh_token: refresh,
        expires_at_unix: now_unix() + parsed.expires_in,
        role: parsed.user.role,
    };
    let host = host_for_keyring(base);
    write_session(&host, &updated)?;
    Ok(updated)
}

#[derive(Clone)]
pub struct AuthCoordinator {
    pub refresh_mtx: Arc<tokio::sync::Mutex<()>>,
}

impl Default for AuthCoordinator {
    fn default() -> Self {
        Self {
            refresh_mtx: Arc::new(tokio::sync::Mutex::new(())),
        }
    }
}

async fn ensure_fresh_session(base: &str, coordinator: &AuthCoordinator) -> Result<AuthSession, String> {
    let host = host_for_keyring(base);
    let session = read_session(&host)?.ok_or_else(|| "session_expired".to_string())?;
    if session.expires_at_unix > now_unix() {
        return Ok(session);
    }
    let _guard = coordinator.refresh_mtx.lock().await;
    let session = read_session(&host)?.ok_or_else(|| "session_expired".to_string())?;
    if session.expires_at_unix > now_unix() {
        return Ok(session);
    }
    refresh_session(base, &session).await
}

type SearchHttpParts = (StatusCode, String, Vec<u8>);

async fn send_memory_search(
    base: &str,
    q: &str,
    bearer: Option<&str>,
    cancel: CancellationToken,
) -> Result<SearchHttpParts, String> {
    let url = format!(
        "{}/api/v1/memory/search?q={}&limit=5",
        normalise_base_url(base.to_string()),
        urlencoding::encode(q)
    );
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| e.to_string())?;
    let mut req = client.get(&url);
    if let Some(token) = bearer {
        if !token.is_empty() {
            req = req.header("Authorization", format!("Bearer {}", token));
        }
    }
    tokio::select! {
        _ = cancel.cancelled() => Err("cancelled".into()),
        res = req.send() => {
            let resp = res.map_err(map_reqwest_error)?;
            let status = resp.status();
            let ctype = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_string();
            let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
            Ok((status, ctype, bytes.to_vec()))
        }
    }
}

fn map_search_http(status: StatusCode, ctype: &str, bytes: &[u8]) -> Result<MemorySearchResponseDto, String> {
    if status == StatusCode::UNAUTHORIZED {
        return Err("http_401".into());
    }
    if status == StatusCode::FORBIDDEN {
        return Err("http_403".into());
    }
    if status == StatusCode::TOO_MANY_REQUESTS {
        return Err("http_429".into());
    }
    if status.is_server_error() {
        return Err("http_5xx".into());
    }
    if !status.is_success() {
        return Err(format!("http_{}", status.as_u16()));
    }
    if !ctype.contains("json") && !bytes.is_empty() && bytes.starts_with(b"<") {
        return Err("non_json_200".into());
    }
    serde_json::from_slice::<MemorySearchResponseDto>(bytes).map_err(|_| "invalid_json".into())
}

pub async fn authenticated_memory_search(
    base: &str,
    q: &str,
    auth_mode: AuthMode,
    coordinator: &AuthCoordinator,
    cancel: CancellationToken,
) -> Result<MemorySearchResponseDto, String> {
    let bearer = if auth_mode == AuthMode::On {
        let session = ensure_fresh_session(base, coordinator).await?;
        Some(session.access_token)
    } else {
        None
    };
    let bearer_ref = bearer.as_deref();
    let (status, ctype, bytes) = send_memory_search(base, q, bearer_ref, cancel.clone()).await?;
    if auth_mode == AuthMode::On && status == StatusCode::UNAUTHORIZED {
        let host = host_for_keyring(base);
        let session = read_session(&host)?.ok_or_else(|| "session_expired".to_string())?;
        let _guard = coordinator.refresh_mtx.lock().await;
        let refreshed = refresh_session(base, &session).await?;
        let (status2, ctype2, bytes2) =
            send_memory_search(base, q, Some(&refreshed.access_token), cancel).await?;
        if status2 == StatusCode::UNAUTHORIZED {
            let _ = clear_session(&host);
            return Err("session_expired".into());
        }
        return map_search_http(status2, &ctype2, &bytes2);
    }
    map_search_http(status, &ctype, &bytes)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AdminSettingsPublic {
    pub ingest_paths: Vec<String>,
    pub pending_ingest_paths: Option<Vec<String>>,
    pub restart_required: bool,
    pub paperless_configured: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IngestUploadQueuedPublic {
    pub status: String,
    pub file_id: String,
}

#[derive(Debug, Serialize)]
struct PutIngestPathsBody {
    ingest_paths: Vec<String>,
}

async fn send_authenticated_with_base(
    method: reqwest::Method,
    base: &str,
    path: &str,
    auth_mode: AuthMode,
    coordinator: &AuthCoordinator,
    body: Option<serde_json::Value>,
) -> Result<SearchHttpParts, String> {
    let base_norm = normalise_base_url(base.to_string());
    let url = format!("{base_norm}{path}");
    let client = http_client()?;

    let send_once = |token: Option<&str>| {
        let mut req = client.request(method.clone(), &url);
        if let Some(t) = token {
            if !t.is_empty() {
                req = req.header("Authorization", format!("Bearer {}", t));
            }
        }
        if let Some(ref json_body) = body {
            req = req.json(json_body);
        }
        req
    };

    let bearer = if auth_mode == AuthMode::On {
        Some(ensure_fresh_session(base, coordinator).await?.access_token)
    } else {
        None
    };
    let bearer_ref = bearer.as_deref();
    let resp = send_once(bearer_ref)
        .send()
        .await
        .map_err(map_reqwest_error)?;
    let status = resp.status();
    let ctype = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;

    if auth_mode == AuthMode::On && status == StatusCode::UNAUTHORIZED {
        let host = host_for_keyring(base);
        let session = read_session(&host)?.ok_or_else(|| "session_expired".to_string())?;
        let _guard = coordinator.refresh_mtx.lock().await;
        let refreshed = refresh_session(base, &session).await?;
        let resp2 = send_once(Some(&refreshed.access_token))
            .send()
            .await
            .map_err(map_reqwest_error)?;
        let status2 = resp2.status();
        let ctype2 = resp2
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let bytes2 = resp2.bytes().await.map_err(|e| e.to_string())?;
        if status2 == StatusCode::UNAUTHORIZED {
            let _ = clear_session(&host);
            return Err("session_expired".into());
        }
        return Ok((status2, ctype2, bytes2.to_vec()));
    }

    Ok((status, ctype, bytes.to_vec()))
}

fn map_admin_http(status: StatusCode, bytes: &[u8]) -> Result<(), String> {
    if status == StatusCode::UNAUTHORIZED {
        return Err("session_expired".into());
    }
    if status == StatusCode::FORBIDDEN {
        return Err("http_403".into());
    }
    if status.is_server_error() {
        return Err("http_5xx".into());
    }
    if !status.is_success() {
        let detail = String::from_utf8_lossy(bytes);
        return Err(format!("http_{}: {}", status.as_u16(), detail.trim()));
    }
    Ok(())
}

pub async fn fetch_admin_settings(
    base: &str,
    auth_mode: AuthMode,
    coordinator: &AuthCoordinator,
) -> Result<AdminSettingsPublic, String> {
    let (status, _ctype, bytes) = send_authenticated_with_base(
        reqwest::Method::GET,
        base,
        "/settings",
        auth_mode,
        coordinator,
        None,
    )
    .await?;
    map_admin_http(status, &bytes)?;
    #[derive(Deserialize)]
    struct Raw {
        ingest_paths: Vec<String>,
        pending_ingest_paths: Option<Vec<String>>,
        restart_required: bool,
        paperless_configured: bool,
    }
    let raw: Raw = serde_json::from_slice(&bytes).map_err(|_| "invalid_json".to_string())?;
    Ok(AdminSettingsPublic {
        ingest_paths: raw.ingest_paths,
        pending_ingest_paths: raw.pending_ingest_paths,
        restart_required: raw.restart_required,
        paperless_configured: raw.paperless_configured,
    })
}

pub async fn put_admin_ingest_paths(
    base: &str,
    auth_mode: AuthMode,
    coordinator: &AuthCoordinator,
    ingest_paths: Vec<String>,
) -> Result<AdminSettingsPublic, String> {
    let body = PutIngestPathsBody { ingest_paths };
    let (status, _ctype, bytes) = send_authenticated_with_base(
        reqwest::Method::PUT,
        base,
        "/settings",
        auth_mode,
        coordinator,
        Some(serde_json::to_value(&body).map_err(|e| e.to_string())?),
    )
    .await?;
    map_admin_http(status, &bytes)?;
    #[derive(Deserialize)]
    struct Raw {
        ingest_paths: Vec<String>,
        pending_ingest_paths: Option<Vec<String>>,
        restart_required: bool,
        paperless_configured: bool,
    }
    let raw: Raw = serde_json::from_slice(&bytes).map_err(|_| "invalid_json".to_string())?;
    Ok(AdminSettingsPublic {
        ingest_paths: raw.ingest_paths,
        pending_ingest_paths: raw.pending_ingest_paths,
        restart_required: raw.restart_required,
        paperless_configured: raw.paperless_configured,
    })
}

pub async fn restart_orchestrator_stack(
    base: &str,
    auth_mode: AuthMode,
    coordinator: &AuthCoordinator,
) -> Result<(), String> {
    let (status, _ctype, bytes) = send_authenticated_with_base(
        reqwest::Method::POST,
        base,
        "/settings/restart",
        auth_mode,
        coordinator,
        None,
    )
    .await?;
    if status == StatusCode::UNAUTHORIZED {
        return Err("session_expired".into());
    }
    if status == StatusCode::FORBIDDEN {
        return Err("http_403".into());
    }
    // Restart recreates the orchestrator container — connection errors are expected.
    if status.is_success() || status.is_server_error() {
        return Ok(());
    }
    let _ = bytes;
    Err(format!("http_{}", status.as_u16()))
}

pub async fn upload_ingest_file(
    base: &str,
    auth_mode: AuthMode,
    coordinator: &AuthCoordinator,
    file_name: String,
    bytes: Vec<u8>,
) -> Result<IngestUploadQueuedPublic, String> {
    if bytes.is_empty() {
        return Err("empty_file".into());
    }
    let url = format!(
        "{}/api/v1/ingest/upload",
        normalise_base_url(base.to_string())
    );
    let client = upload_http_client()?;

    let send_multipart = |token: Option<&str>| {
        let part = reqwest::multipart::Part::bytes(bytes.clone()).file_name(file_name.clone());
        let form = reqwest::multipart::Form::new().part("file", part);
        let mut req = client.post(&url).multipart(form);
        if let Some(t) = token {
            if !t.is_empty() {
                req = req.header("Authorization", format!("Bearer {}", t));
            }
        }
        req
    };

    let bearer = if auth_mode == AuthMode::On {
        Some(ensure_fresh_session(base, coordinator).await?.access_token)
    } else {
        None
    };
    let resp = send_multipart(bearer.as_deref())
        .send()
        .await
        .map_err(map_reqwest_error)?;
    let status = resp.status();
    let body_bytes = resp.bytes().await.map_err(|e| e.to_string())?;

    if auth_mode == AuthMode::On && status == StatusCode::UNAUTHORIZED {
        let host = host_for_keyring(base);
        let session = read_session(&host)?.ok_or_else(|| "session_expired".to_string())?;
        let _guard = coordinator.refresh_mtx.lock().await;
        let refreshed = refresh_session(base, &session).await?;
        let resp2 = send_multipart(Some(&refreshed.access_token))
            .send()
            .await
            .map_err(map_reqwest_error)?;
        let status2 = resp2.status();
        let body_bytes2 = resp2.bytes().await.map_err(|e| e.to_string())?;
        if status2 == StatusCode::UNAUTHORIZED {
            let _ = clear_session(&host);
            return Err("session_expired".into());
        }
        return map_upload_http(status2, &body_bytes2);
    }

    map_upload_http(status, &body_bytes)
}

fn map_upload_http(status: StatusCode, bytes: &[u8]) -> Result<IngestUploadQueuedPublic, String> {
    if status == StatusCode::UNAUTHORIZED {
        return Err("session_expired".into());
    }
    if status == StatusCode::FORBIDDEN {
        return Err("http_403".into());
    }
    if status == StatusCode::UNSUPPORTED_MEDIA_TYPE {
        return Err("unsupported_extension".into());
    }
    if status.as_u16() == 413 {
        return Err("file_too_large".into());
    }
    if status == StatusCode::UNPROCESSABLE_ENTITY {
        return Err("missing_file".into());
    }
    if status.is_server_error() {
        return Err("http_5xx".into());
    }
    if status != StatusCode::ACCEPTED {
        return Err(format!("http_{}", status.as_u16()));
    }
    #[derive(Deserialize)]
    struct Raw {
        status: String,
        file_id: String,
    }
    let raw: Raw = serde_json::from_slice(bytes).map_err(|_| "invalid_json".to_string())?;
    Ok(IngestUploadQueuedPublic {
        status: raw.status,
        file_id: raw.file_id,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::header::HeaderValue;

    #[test]
    fn parse_refresh_from_set_cookie() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "set-cookie",
            HeaderValue::from_static(
                "lumogis_refresh=abc123.def; HttpOnly; Path=/api/v1/auth; SameSite=strict",
            ),
        );
        assert_eq!(
            parse_lumogis_refresh_from_headers(&headers).as_deref(),
            Some("abc123.def")
        );
    }

    #[test]
    fn probe_status_maps_auth_modes() {
        assert_eq!(AuthMode::from_probe_status(StatusCode::OK), AuthMode::Off);
        assert_eq!(
            AuthMode::from_probe_status(StatusCode::UNAUTHORIZED),
            AuthMode::On
        );
        assert_eq!(
            AuthMode::from_probe_status(StatusCode::BAD_GATEWAY),
            AuthMode::Unreachable
        );
    }
}
